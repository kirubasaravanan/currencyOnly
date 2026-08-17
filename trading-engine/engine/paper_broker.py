"""Local-only simulated broker. Structurally zero order-placement code —
no import of any broker order-placement client, no HTTP call to any
`/orders` endpoint anywhere. Every position here is entirely in-process.

Commission model (explicit user choice: raw spread + per-lot commission,
ECN/prop-firm style — see config.py's RAW_SPREAD_PIPS and
COMMISSION_PER_LOT_PER_SIDE_USD, both estimated placeholders pending
FundedNext's real published rates). Commission is charged on both entry
and exit and tracked as its own `commission_paid` field, separate from
`pnl`, so gross vs. commission-drag vs. net is always visible.

The profit-based trailing stop and 50%-partial-then-runner-to-TP2 design
are direct ports of V109's own "SINGLE-TRADE SCALE-OUT EDITION" — the live
Forex/Forex app disabled this (PAPER_SL_DYNAMICS_ENABLED = False) because
its real broker relay can't modify SL/TP mid-trade.

[UPDATED 2026-08-16] This app now optionally does relay too (see
engine/tradesgnl_relay.py), and hit the identical problem: TradeSgnl also
can't modify an already-open order's SL/TP, so the config.EXIT_MODE
toggle now exists so the SAME logic runs identically here and on the
relay side, instead of the two silently diverging. See config.EXIT_MODE's
own comment for the full story (found via a real live mismatch, not
theoretical) — "dynamic" is this module's original partial+trailing
design, "static" is a plain fixed SL/TP with no partial and no trailing,
selectable at runtime to A/B them against each other on real outcomes.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
from config import (
    INITIAL_BALANCE,
    CONTRACT_SIZE_USD,
    PIP_SIZE,
    RAW_SPREAD_PIPS,
    COMMISSION_PER_LOT_PER_SIDE_USD,
    BE_OFFSET_PCT,
    EARLY_BE_TRIGGER_PCT,
    PAIR_CALIBRATION,
)
from engine.fx_conversion import usd_conversion_rate

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "storage", "broker_state.json")


class PaperBroker:
    def __init__(self) -> None:
        self.equity = INITIAL_BALANCE
        self.peak_equity = INITIAL_BALANCE
        self.balance = INITIAL_BALANCE
        self.open_positions: List[Dict] = []
        self.closed_trades: List[Dict] = []
        self.trade_counter = 1
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        path = os.path.abspath(STATE_FILE)
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.equity = data.get("equity", INITIAL_BALANCE)
            self.peak_equity = data.get("peak_equity", INITIAL_BALANCE)
            self.balance = data.get("balance", INITIAL_BALANCE)
            self.open_positions = data.get("open_positions", [])
            self.closed_trades = data.get("closed_trades", [])
            self.trade_counter = data.get("trade_counter", 1)
        except Exception as exc:  # noqa: BLE001
            print(f"[paper_broker] state load failed: {exc}")

    def _save(self) -> None:
        path = os.path.abspath(STATE_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "balance": self.balance,
            "open_positions": self.open_positions,
            "closed_trades": self.closed_trades[-500:],
            "trade_counter": self.trade_counter,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # ------------------------------------------------------------------
    def open_trade(self, signal: Dict, lots: float) -> Optional[Dict]:
        symbol = signal["symbol"]
        is_long = signal["side"] == "BULLISH"
        direction = 1 if is_long else -1
        pip = PIP_SIZE[symbol]
        spread_pips = RAW_SPREAD_PIPS.get(symbol, 1.0)
        fill_price = signal["entry_price"] + direction * spread_pips * pip / 2

        entry_commission = round(COMMISSION_PER_LOT_PER_SIDE_USD * lots, 2)
        self.balance -= entry_commission

        sl_dist = abs(fill_price - signal["sl_price"])
        tp1_dist = abs(signal["tp_price"] - signal["entry_price"])
        tp2_dist = abs(signal["tp2_price"] - signal["entry_price"])

        trade = {
            "id": self.trade_counter,
            "symbol": symbol,
            "side": signal["side"],
            "lots": lots,
            "original_lots": lots,
            "entry_price": round(fill_price, 6),
            "sl_price": round(signal["sl_price"], 6),
            "original_sl_price": round(signal["sl_price"], 6),
            "sl_dist": round(sl_dist, 6),
            "tp_price": round(signal["tp_price"], 6),
            "tp2_price": round(signal["tp2_price"], 6),
            "tp1_dist": round(tp1_dist, 6),
            "tp2_dist": round(tp2_dist, 6),
            "rr": signal.get("rr"),
            "confidence": signal.get("confidence"),
            "atr": signal.get("atr"),
            "session": signal.get("session"),
            "reasons": signal.get("reasons", {}),
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "status": "open",
            "pnl": 0.0,
            "pnl_pips": 0.0,
            "peak_pnl": 0.0,
            "peak_favorable_move": 0.0,
            "be_moved": False,
            "partial_taken": False,
            "partial_pnl_gross": 0.0,
            "partial_pnl_net": 0.0,
            "entry_commission": entry_commission,
            "partial_commission": 0.0,
            "commission_paid": entry_commission,
        }
        self.open_positions.append(trade)
        self.trade_counter += 1
        self._save()
        return trade

    # ------------------------------------------------------------------
    def mark_to_market(self, prices: Dict[str, float]) -> None:
        # Iterate a snapshot copy — close_trade()/_take_partial() below
        # mutate self.open_positions mid-loop.
        for t in list(self.open_positions):
            price = prices.get(t["symbol"])
            if price is None:
                continue
            contract = CONTRACT_SIZE_USD[t["symbol"]]
            direction = 1 if t["side"] == "BULLISH" else -1
            pip = PIP_SIZE[t["symbol"]]
            conv = usd_conversion_rate(t["symbol"], prices) or 1.0

            unrealized_gross = direction * (price - t["entry_price"]) * contract * t["lots"] * conv
            t["pnl"] = round(unrealized_gross + t["partial_pnl_net"] - t["entry_commission"], 2)
            t["pnl_pips"] = round(direction * (price - t["entry_price"]) / pip, 1)
            t["peak_pnl"] = max(t["peak_pnl"], t["pnl"])

            favorable_move = direction * (price - t["entry_price"])
            t["peak_favorable_move"] = max(t["peak_favorable_move"], favorable_move)

            dynamic = config.state.exit_mode == "dynamic"
            if dynamic:
                self._maybe_move_to_breakeven(t, favorable_move)
                self._update_trailing_stop(t, favorable_move)

            sl, tp = t["sl_price"], t["tp_price"]
            if direction == 1:
                if price <= sl:
                    reason = "break_even" if t["be_moved"] else "stop_loss"
                    self.close_trade(t["id"], sl, reason, prices)
                    continue
                if price >= tp:
                    if not dynamic:
                        self.close_trade(t["id"], tp, "take_profit", prices)
                        continue
                    if not t["partial_taken"]:
                        self._take_partial(t, tp, 0.5, prices)
                        continue
                    if price >= t["tp2_price"]:
                        self.close_trade(t["id"], t["tp2_price"], "take_profit_runner", prices)
                        continue
            else:
                if price >= sl:
                    reason = "break_even" if t["be_moved"] else "stop_loss"
                    self.close_trade(t["id"], sl, reason, prices)
                    continue
                if price <= tp:
                    if not dynamic:
                        self.close_trade(t["id"], tp, "take_profit", prices)
                        continue
                    if not t["partial_taken"]:
                        self._take_partial(t, tp, 0.5, prices)
                        continue
                    if price <= t["tp2_price"]:
                        self.close_trade(t["id"], t["tp2_price"], "take_profit_runner", prices)
                        continue

        self.equity = round(self.balance + sum(t["pnl"] for t in self.open_positions), 2)
        self.peak_equity = max(self.peak_equity, self.equity)
        self._save()

    # ------------------------------------------------------------------
    def _maybe_move_to_breakeven(self, t: Dict, favorable_move: float) -> None:
        """V109's earlyBE: move SL to breakeven+offset once price is 90% of
        the way to TP1, even before TP1 itself is hit."""
        if t["be_moved"] or t["partial_taken"]:
            return
        if favorable_move < t["tp1_dist"] * EARLY_BE_TRIGGER_PCT:
            return
        direction = 1 if t["side"] == "BULLISH" else -1
        be_price = t["entry_price"] + direction * (t["sl_dist"] * BE_OFFSET_PCT)
        new_sl = max(t["sl_price"], be_price) if direction == 1 else min(t["sl_price"], be_price)
        if new_sl != t["sl_price"]:
            t["sl_price"] = round(new_sl, 6)
            t["be_moved"] = True

    def _update_trailing_stop(self, t: Dict, favorable_move: float) -> None:
        """V109's dynamic profit-trailing stop: activates once peak
        favorable move clears activation_pct of the current target
        distance, then ratchets the lock percentage up in steps
        (60/70/80/90%) as price closes in on that target. Simplified vs.
        V109's own dual $-or-%-of-target activation threshold — this uses
        %-of-target only, since the $ leg needs a lots/FX-conversion-aware
        price-per-dollar figure that adds real complexity for a threshold
        that's explicitly a fine-tune-later knob."""
        calib = PAIR_CALIBRATION[t["symbol"]]
        direction = 1 if t["side"] == "BULLISH" else -1
        target_dist = t["tp2_dist"] if t["partial_taken"] else t["tp1_dist"]
        peak = t["peak_favorable_move"]
        if target_dist <= 0 or peak <= 0:
            return

        activation_threshold = (calib.activation_pct / 100.0) * target_dist
        if peak < activation_threshold:
            return

        peak_pct_of_target = (peak / target_dist) * 100.0
        lock_pct = calib.trail_lock_pct
        for pct_threshold in (90.0, 80.0, 70.0, 60.0):
            if peak_pct_of_target >= pct_threshold:
                lock_pct = max(lock_pct, pct_threshold)
                break
        locked_profit_dist = (lock_pct / 100.0) * peak

        trailing_price = t["entry_price"] + direction * locked_profit_dist
        new_sl = max(t["sl_price"], trailing_price) if direction == 1 else min(t["sl_price"], trailing_price)
        if new_sl != t["sl_price"]:
            t["sl_price"] = round(new_sl, 6)
            t["be_moved"] = True

    # ------------------------------------------------------------------
    def _take_partial(self, t: Dict, price: float, fraction: float, prices: Optional[Dict[str, float]] = None) -> None:
        contract = CONTRACT_SIZE_USD[t["symbol"]]
        direction = 1 if t["side"] == "BULLISH" else -1
        conv = usd_conversion_rate(t["symbol"], prices) or 1.0

        partial_lots = round(t["lots"] * fraction, 2)
        gross = direction * (price - t["entry_price"]) * contract * partial_lots * conv
        commission = round(COMMISSION_PER_LOT_PER_SIDE_USD * partial_lots, 2)
        net = round(gross - commission, 2)

        self.balance += net
        t["partial_pnl_gross"] = round(gross, 2)
        t["partial_pnl_net"] = net
        t["partial_commission"] = commission
        t["commission_paid"] = round(t["entry_commission"] + commission, 2)
        t["lots"] = round(t["lots"] - partial_lots, 2)
        t["partial_taken"] = True

        be_price = t["entry_price"] + direction * (t["sl_dist"] * BE_OFFSET_PCT)
        t["sl_price"] = round(max(t["sl_price"], be_price) if direction == 1 else min(t["sl_price"], be_price), 6)
        t["be_moved"] = True
        self._save()

    # ------------------------------------------------------------------
    def close_trade(self, trade_id: int, exit_price: float, reason: str = "manual", prices: Optional[Dict[str, float]] = None) -> Optional[Dict]:
        for i, t in enumerate(self.open_positions):
            if t["id"] != trade_id:
                continue
            contract = CONTRACT_SIZE_USD[t["symbol"]]
            direction = 1 if t["side"] == "BULLISH" else -1
            conv = usd_conversion_rate(t["symbol"], prices) or 1.0

            remaining_gross = direction * (exit_price - t["entry_price"]) * contract * t["lots"] * conv
            exit_commission = round(COMMISSION_PER_LOT_PER_SIDE_USD * t["lots"], 2)
            remaining_net = round(remaining_gross - exit_commission, 2)
            self.balance += remaining_net

            pnl_gross_total = round(t["partial_pnl_gross"] + remaining_gross, 2)
            commission_total = round(t["commission_paid"] + exit_commission, 2)
            pnl_net_total = round(pnl_gross_total - commission_total, 2)

            t.update({
                "exit_price": round(exit_price, 6),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "pnl_gross": pnl_gross_total,
                "commission_paid": commission_total,
                "pnl": pnl_net_total,
                "status": "closed",
            })
            self.closed_trades.append(t)
            self.open_positions.pop(i)
            self._save()
            return t
        return None

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.equity = INITIAL_BALANCE
        self.peak_equity = INITIAL_BALANCE
        self.balance = INITIAL_BALANCE
        self.open_positions = []
        self.closed_trades = []
        self.trade_counter = 1
        self._save()


broker = PaperBroker()
