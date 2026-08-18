"""One-off, read-only research script — NOT part of the live engine.

Answers a question the standard backtester can't: for every trade, how far
underwater did it go before eventually closing (win or loss), and how many
bars into the trade did that worst point happen? paper_broker.py only
tracks FAVORABLE excursion (peak_pnl/peak_favorable_move, used by the
trailing-stop/break-even logic) — there's no equivalent adverse-excursion
tracking anywhere, so this can't be answered from stored trade data alone.

Reuses backtester.py's exact fetch/gate/risk/broker pipeline unmodified
(same entry_signal/risk/correlation/trade_manager calls, same
BacktestBroker) — this script only ADDS a thin wrapper around
mark_to_market() that records the worst unrealized $ a trade reached, and
how many 15m bars into its life that happened, before letting the real
close logic run on the same tick. Never touches config.py, paper_broker.py,
or any live-engine file. Safe to delete after use.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

sys.path.insert(0, r"C:\currencyOnly\trading-engine")

from config import PAIRS, ENTRY_TIMEFRAME, CONTRACT_SIZE_USD  # noqa: E402
from data.market_data import market_data  # noqa: E402
from engine import risk, correlation, trade_manager, currency_strength  # noqa: E402
from engine.entry import entry_signal  # noqa: E402
from engine.backtester import (  # noqa: E402
    BacktestBroker, _fetch_symbol_frames, _window, WARMUP_BARS,
)
from engine.fx_conversion import usd_conversion_rate  # noqa: E402
from engine.analytics import compute_stats  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60


class MAEBroker(BacktestBroker):
    def open_trade(self, signal: Dict, lots: float, now: Optional[datetime] = None):
        trade = super().open_trade(signal, lots, now=now)
        if trade:
            trade["peak_adverse_pnl"] = 0.0
            trade["bars_to_worst"] = 0
            trade["_bar_count"] = 0
        return trade

    def mark_to_market(self, prices: Dict[str, float]) -> None:
        for t in self.open_positions:
            symbol = t["symbol"]
            price = prices.get(symbol)
            t["_bar_count"] = t.get("_bar_count", 0) + 1
            if price is None:
                continue
            direction = 1 if t["side"] == "BULLISH" else -1
            contract = CONTRACT_SIZE_USD[symbol]
            conv = usd_conversion_rate(symbol, prices)
            if conv is None:
                continue
            unrealized_gross = direction * (price - t["entry_price"]) * contract * t["lots"] * conv
            if unrealized_gross < t.get("peak_adverse_pnl", 0.0):
                t["peak_adverse_pnl"] = round(unrealized_gross, 2)
                t["bars_to_worst"] = t["_bar_count"]
        super().mark_to_market(prices)


async def run() -> Dict:
    frames_by_symbol = {}
    for symbol in PAIRS:
        frames_by_symbol[symbol] = await _fetch_symbol_frames(symbol, DAYS)

    all_times = sorted(set().union(*[
        set(f["15m"].index) for f in frames_by_symbol.values() if not f["15m"].empty
    ])) if frames_by_symbol else []
    if not all_times:
        return {"error": "no_data"}

    broker = MAEBroker()
    broker.reset()
    total_bars = len(all_times)
    block_reasons: Dict[str, int] = {}
    first_block_ts = {}
    funnel = {"candidates": 0, "cooldown": 0, "insufficient_ctx": 0, "signal_none": 0, "exposure_blocked": 0, "opened": 0}

    for idx, ts in enumerate(all_times):
        if idx < WARMUP_BARS:
            continue
        now = ts.to_pydatetime()

        prices: Dict[str, float] = {}
        for symbol, frames in frames_by_symbol.items():
            w = _window(frames["15m"], ts, max_bars=1)
            if not w.empty:
                prices[symbol] = float(w["close"].iloc[-1])
        if not prices:
            continue

        broker.set_sim_time(now)
        trade_manager.manage_open_positions(broker, prices, now)

        w1h_by_symbol = {s: _window(f["1h"], ts) for s, f in frames_by_symbol.items()}
        ranking = currency_strength.compute_ranking(w1h_by_symbol)

        open_symbols = {t["symbol"] for t in broker.open_positions}
        for symbol, frames in frames_by_symbol.items():
            if symbol in open_symbols or symbol not in prices:
                continue
            funnel["candidates"] += 1
            if trade_manager.in_cooldown(symbol, now, broker.closed_trades):
                funnel["cooldown"] += 1
                continue
            risk_check = risk.can_open_new_trade(broker.open_positions, broker.closed_trades, broker.equity, broker.peak_equity)
            if not risk_check["allowed"]:
                reason = risk_check["reason"]
                block_reasons[reason] = block_reasons.get(reason, 0) + 1
                if reason not in first_block_ts:
                    first_block_ts[reason] = str(now)
                continue

            w15 = _window(frames["15m"], ts)
            if len(w15) < WARMUP_BARS:
                funnel["insufficient_ctx"] += 1
                continue
            w1h = w1h_by_symbol[symbol]
            w4h = _window(frames["4h"], ts)
            w1d = _window(frames["1d"], ts, max_bars=60)
            w5m = _window(frames["5m"], ts, max_bars=900)
            if len(w1h) < 55 or len(w4h) < 55:
                funnel["insufficient_ctx"] += 1
                continue

            signal = entry_signal(symbol, {"15m": w15, "1h": w1h, "4h": w4h, "1d": w1d, "5m": w5m}, now=now, currency_ranking=ranking)
            if signal is None:
                funnel["signal_none"] += 1
                continue
            exposure = correlation.would_exceed_exposure(symbol, signal["side"], broker.open_positions)
            if exposure["blocked"]:
                funnel["exposure_blocked"] += 1
                continue
            sizing = risk.position_size(symbol, broker.equity, signal["entry_price"], signal["sl_price"], signal.get("size_multiplier", 1.0), prices)
            broker.open_trade(signal, sizing["lots"], now=now)
            open_symbols.add(symbol)
            funnel["opened"] += 1

        if idx % 500 == 0:
            print(f"progress: {idx}/{total_bars}", file=sys.stderr)

    final_prices = {s: float(f["15m"]["close"].iloc[-1]) for s, f in frames_by_symbol.items() if not f["15m"].empty}
    for t in list(broker.open_positions):
        price = final_prices.get(t["symbol"], t["entry_price"])
        broker.close_trade(t["id"], price, "backtest_end", final_prices, now=all_times[-1].to_pydatetime())

    print(f"BLOCK REASONS: {block_reasons}", file=sys.stderr)
    print(f"FIRST BLOCK AT (simulated time): {first_block_ts}", file=sys.stderr)
    print(f"FUNNEL: {funnel}", file=sys.stderr)
    return {"stats": compute_stats(broker), "closed_trades": broker.closed_trades}


def _duration_minutes(t: Dict) -> Optional[float]:
    try:
        o = datetime.fromisoformat(t["opened_at"])
        c = datetime.fromisoformat(t["closed_at"])
        return (c - o).total_seconds() / 60.0
    except Exception:
        return None


def summarize(closed: List[Dict]) -> None:
    by_symbol: Dict[str, Dict] = {}
    for t in closed:
        sym = t["symbol"]
        row = by_symbol.setdefault(sym, {"gp": 0.0, "gl": 0.0, "wins": 0, "losses": 0, "trades": 0})
        row["trades"] += 1
        pnl = t.get("pnl", 0.0)
        if pnl > 0:
            row["gp"] += pnl
            row["wins"] += 1
        elif pnl < 0:
            row["gl"] += -pnl
            row["losses"] += 1

    print("\n=== PER-PAIR PF / WIN% (60 days) ===")
    print(f"{'PAIR':<8}{'trades':>7}{'win%':>7}{'PF':>8}{'net$':>10}")
    for sym in PAIRS:
        row = by_symbol.get(sym)
        if not row or row["trades"] == 0:
            print(f"{sym:<8}{'0':>7}{'-':>7}{'-':>8}{'-':>10}")
            continue
        win_pct = 100 * row["wins"] / row["trades"]
        pf = (row["gp"] / row["gl"]) if row["gl"] > 0 else (float("inf") if row["gp"] > 0 else 0.0)
        net = row["gp"] - row["gl"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{sym:<8}{row['trades']:>7}{win_pct:>6.1f}%{pf_str:>8}{net:>10.2f}")

    durations = [_duration_minutes(t) for t in closed if _duration_minutes(t) is not None]
    wins = [t for t in closed if t.get("pnl", 0.0) > 0]
    losses = [t for t in closed if t.get("pnl", 0.0) <= 0]
    win_durs = [_duration_minutes(t) for t in wins if _duration_minutes(t) is not None]
    loss_durs = [_duration_minutes(t) for t in losses if _duration_minutes(t) is not None]

    print("\n=== DURATION (minutes) ===")
    print(f"all trades avg: {sum(durations)/len(durations):.1f}" if durations else "no data")
    print(f"winners avg:    {sum(win_durs)/len(win_durs):.1f}" if win_durs else "no winners")
    print(f"losers avg:     {sum(loss_durs)/len(loss_durs):.1f}" if loss_durs else "no losers")

    win_mae = [t.get("peak_adverse_pnl", 0.0) for t in wins]
    loss_mae = [t.get("peak_adverse_pnl", 0.0) for t in losses]
    win_bars = [t.get("bars_to_worst", 0) for t in wins]
    loss_bars = [t.get("bars_to_worst", 0) for t in losses]

    print("\n=== ADVERSE EXCURSION (worst unrealized $ reached before close) ===")
    print(f"winners avg worst dip: ${sum(win_mae)/len(win_mae):.2f}" if win_mae else "no winners")
    print(f"losers avg worst dip:  ${sum(loss_mae)/len(loss_mae):.2f}" if loss_mae else "no losers")
    print(f"winners avg bars-to-worst-point (x15min): {sum(win_bars)/len(win_bars):.1f}" if win_bars else "")
    print(f"losers avg bars-to-worst-point (x15min):  {sum(loss_bars)/len(loss_bars):.1f}" if loss_bars else "")

    print(f"\ntotal trades: {len(closed)}, wins: {len(wins)}, losses: {len(losses)}")


if __name__ == "__main__":
    result = asyncio.run(run())
    if "error" in result:
        print(result)
    else:
        summarize(result["closed_trades"])
        stats = result["stats"]
        print("\n=== OVERALL ===")
        print({k: v for k, v in stats.items() if k != "by_symbol"})
