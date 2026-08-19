"""One-off, read-only research script -- NOT part of the live engine.

Answers the user's question (2026-08-19): while a trade is open for hours,
the live orchestrator never re-evaluates entry_signal() for that symbol --
it explicitly skips any symbol already in open_positions -- so it has zero
visibility into whether the market has since produced a full-gate signal in
the OPPOSITE direction. Tests two candidate responses against a real
walk-forward replay, compared against the actual (unmodified) trade
outcomes:

  A) CLOSE on a confirmed reversal -- a full-gate (both Layer-1 hard gate
     AND Layer-2 confluence), opposite-side signal that holds for
     REVERSAL_CONFIRM_BARS consecutive 15m bars (filters single-bar noise;
     the user explicitly did not want a close-on-any-blip rule).
  B) TIGHTEN the stop toward current price on the same confirmed signal
     instead of closing outright -- caps further downside but leaves the
     door open for a recovery. Ratchets tighter each additional bar the
     reversal keeps confirming; never loosens the existing stop.

Also answers the user's follow-up: after a reversal-triggered close, does
the market's *next* trade on that same symbol resume the ORIGINAL
direction (a chop/whipsaw signature -- argues for NOT direction-
constraining re-entry, since the "reversal" was noise) or the NEW
direction (argues re-entry could safely be constrained to it)? Reports
real per-trade outcomes for both buckets rather than a guess.

Reuses the exact live/backtest code path (engine.backtester's own fetch
helper, BacktestBroker, entry_signal, trade_manager, risk, correlation) --
this script only adds reversal-detection instrumentation around a copy of
that existing per-bar loop; it does not reimplement any trading logic, and
never runs on the VPS (heavy backtests have crashed the live engine there
before -- see prior session notes) or touches live/paper state.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine import backtester, risk, correlation, trade_manager, currency_strength  # noqa: E402
from engine.entry import entry_signal  # noqa: E402
from engine.analytics import compute_stats  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
REVERSAL_CONFIRM_BARS = 2  # 30 min on 15m bars -- confirmation, not a single-bar blip


async def _fetch_all(days: int) -> Dict[str, Dict]:
    frames_by_symbol: Dict[str, Dict] = {}
    for symbol in backtester.PAIRS:
        frames_by_symbol[symbol] = await backtester._fetch_symbol_frames(symbol, days)
    return frames_by_symbol


def _shared_all_times(frames_by_symbol: Dict[str, Dict]):
    return sorted(set().union(*[
        set(f["15m"].index) for f in frames_by_symbol.values() if not f["15m"].empty
    ])) if frames_by_symbol else []


async def _simulate(frames_by_symbol: Dict, all_times, mode: Optional[str]):
    """mode: None (baseline, unmodified backtester loop), 'close', or
    'tighten'. Returns (broker, reversal_events) -- reversal_events is
    only populated for mode in {'close','tighten'}."""
    broker = backtester.BacktestBroker()
    broker.reset()
    reversal_streak: Dict[int, int] = {}
    reversal_events: List[Dict] = []
    diag_any_signal_while_open = 0
    diag_opposite_signal_while_open = 0
    diag_checks_while_open = 0

    for idx, ts in enumerate(all_times):
        if idx < backtester.WARMUP_BARS:
            continue
        now = ts.to_pydatetime()

        prices: Dict[str, float] = {}
        for symbol, frames in frames_by_symbol.items():
            w = backtester._window(frames["15m"], ts, max_bars=1)
            if not w.empty:
                prices[symbol] = float(w["close"].iloc[-1])
        if not prices:
            continue

        broker.set_sim_time(now)
        trade_manager.manage_open_positions(broker, prices, now)

        w1h_by_symbol = {s: backtester._window(f["1h"], ts) for s, f in frames_by_symbol.items()}
        ranking = currency_strength.compute_ranking(w1h_by_symbol)

        if mode is not None:
            for t in list(broker.open_positions):
                symbol = t["symbol"]
                frames = frames_by_symbol.get(symbol)
                if frames is None or symbol not in prices:
                    continue
                w15 = backtester._window(frames["15m"], ts)
                if len(w15) < backtester.WARMUP_BARS:
                    continue
                w1h = w1h_by_symbol[symbol]
                w4h = backtester._window(frames["4h"], ts)
                w1d = backtester._window(frames["1d"], ts, max_bars=60)
                w5m = backtester._window(frames["5m"], ts, max_bars=backtester.CONTEXT_WINDOW_BARS * 3)
                if len(w1h) < 55 or len(w4h) < 55:
                    continue

                sig = entry_signal(symbol, {"15m": w15, "1h": w1h, "4h": w4h, "1d": w1d, "5m": w5m}, now=now, currency_ranking=ranking)
                diag_checks_while_open += 1
                if sig is not None:
                    diag_any_signal_while_open += 1
                is_opposite = sig is not None and sig["side"] != t["side"]
                if is_opposite:
                    diag_opposite_signal_while_open += 1
                key = t["id"]
                reversal_streak[key] = reversal_streak.get(key, 0) + 1 if is_opposite else 0

                if reversal_streak.get(key, 0) >= REVERSAL_CONFIRM_BARS:
                    price = prices[symbol]
                    if mode == "close":
                        orig_side = t["side"]
                        closed = broker.close_trade(t["id"], price, "signal_reversal", prices, now=now)
                        if closed is not None:
                            reversal_events.append({
                                "symbol": symbol,
                                "closed_at": now,
                                "orig_side": orig_side,
                                "reversal_side": sig["side"],
                                "pnl_at_reversal": closed.get("pnl"),
                            })
                        reversal_streak.pop(key, None)
                    elif mode == "tighten":
                        direction = 1 if t["side"] in ("BUY", "BULLISH") else -1
                        new_sl = max(t["sl_price"], price) if direction == 1 else min(t["sl_price"], price)
                        if new_sl != t["sl_price"]:
                            t["sl_price"] = round(new_sl, 6)
                            reversal_events.append({"symbol": symbol, "at": now, "new_sl": t["sl_price"]})

        open_symbols = {t["symbol"] for t in broker.open_positions}
        for symbol, frames in frames_by_symbol.items():
            if symbol in open_symbols or symbol not in prices:
                continue
            if trade_manager.in_cooldown(symbol, now, broker.closed_trades):
                continue

            risk_check = risk.can_open_new_trade(broker.open_positions, broker.closed_trades, broker.equity, broker.peak_equity)
            if not risk_check["allowed"]:
                continue

            w15 = backtester._window(frames["15m"], ts)
            if len(w15) < backtester.WARMUP_BARS:
                continue
            w1h = w1h_by_symbol[symbol]
            w4h = backtester._window(frames["4h"], ts)
            w1d = backtester._window(frames["1d"], ts, max_bars=60)
            w5m = backtester._window(frames["5m"], ts, max_bars=backtester.CONTEXT_WINDOW_BARS * 3)
            if len(w1h) < 55 or len(w4h) < 55:
                continue

            signal = entry_signal(symbol, {"15m": w15, "1h": w1h, "4h": w4h, "1d": w1d, "5m": w5m}, now=now, currency_ranking=ranking)
            if signal is None:
                continue

            exposure = correlation.would_exceed_exposure(symbol, signal["side"], broker.open_positions)
            if exposure["blocked"]:
                continue

            sizing = risk.position_size(symbol, broker.equity, signal["entry_price"], signal["sl_price"], signal.get("size_multiplier", 1.0), prices)
            broker.open_trade(signal, sizing["lots"], now=now)
            open_symbols.add(symbol)

    final_prices = {s: float(f["15m"]["close"].iloc[-1]) for s, f in frames_by_symbol.items() if not f["15m"].empty}
    for t in list(broker.open_positions):
        price = final_prices.get(t["symbol"], t["entry_price"])
        broker.close_trade(t["id"], price, "backtest_end", final_prices, now=all_times[-1].to_pydatetime())

    if mode is not None:
        print(f"  [diag mode={mode}] checks_while_open={diag_checks_while_open} "
              f"any_signal_while_open={diag_any_signal_while_open} "
              f"opposite_signal_while_open={diag_opposite_signal_while_open}")

    return broker, reversal_events


def _next_trade_after(closed_trades: List[Dict], symbol: str, after: datetime) -> Optional[Dict]:
    candidates = [
        t for t in closed_trades
        if t["symbol"] == symbol and t.get("opened_at")
        and datetime.fromisoformat(t["opened_at"]) > after
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda t: datetime.fromisoformat(t["opened_at"]))


async def main() -> None:
    print(f"Fetching {DAYS}d of data for {len(backtester.PAIRS)} pairs (shared across all 3 runs)...")
    frames_by_symbol = await _fetch_all(DAYS)
    all_times = _shared_all_times(frames_by_symbol)
    if not all_times:
        print({"error": "no_data"})
        return

    print("Running baseline (unmodified, actual behavior)...")
    baseline, _ = await _simulate(frames_by_symbol, all_times, mode=None)
    baseline_stats = compute_stats(baseline)

    print("Running CLOSE-on-confirmed-reversal variant...")
    closed_variant, close_events = await _simulate(frames_by_symbol, all_times, mode="close")
    close_stats = compute_stats(closed_variant)

    print("Running TIGHTEN-stop-on-confirmed-reversal variant...")
    tighten_variant, tighten_events = await _simulate(frames_by_symbol, all_times, mode="tighten")
    tighten_stats = compute_stats(tighten_variant)

    print(f"\n=== BASELINE (actual, no reversal rule) -- {DAYS}d ===")
    print(f"Trades: {baseline_stats['total_trades']}  Win rate: {baseline_stats['win_rate']}%  "
          f"Net P&L: ${baseline_stats['pnl_net']:.2f}  Profit factor: {baseline_stats['profit_factor']}")

    print(f"\n=== VARIANT A: CLOSE on confirmed reversal ({REVERSAL_CONFIRM_BARS} bars = {REVERSAL_CONFIRM_BARS*15}min) ===")
    print(f"Trades: {close_stats['total_trades']}  Win rate: {close_stats['win_rate']}%  "
          f"Net P&L: ${close_stats['pnl_net']:.2f}  Profit factor: {close_stats['profit_factor']}")
    print(f"Reversal-triggered closes: {len(close_events)}")
    if close_events:
        pnls = [e["pnl_at_reversal"] for e in close_events if e["pnl_at_reversal"] is not None]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  Of those: {wins}/{len(pnls)} were still net positive at the moment of the reversal-close "
              f"(avg P&L at close: ${sum(pnls)/len(pnls):.2f})")

    print(f"\n=== VARIANT B: TIGHTEN stop on confirmed reversal ===")
    print(f"Trades: {tighten_stats['total_trades']}  Win rate: {tighten_stats['win_rate']}%  "
          f"Net P&L: ${tighten_stats['pnl_net']:.2f}  Profit factor: {tighten_stats['profit_factor']}")
    print(f"Stop-tightening events: {len(tighten_events)}")

    print(f"\n=== DELTA vs baseline ===")
    print(f"CLOSE variant:   ${close_stats['pnl_net'] - baseline_stats['pnl_net']:+.2f}")
    print(f"TIGHTEN variant: ${tighten_stats['pnl_net'] - baseline_stats['pnl_net']:+.2f}")

    # Follow-up question: after a reversal-close, does the NEXT trade on the
    # same symbol resume the ORIGINAL direction (chop signature) or the NEW
    # (reversal) direction -- and how does each bucket actually perform?
    same_dir_outcomes: List[float] = []
    opp_dir_outcomes: List[float] = []
    no_reentry = 0
    for ev in close_events:
        nxt = _next_trade_after(closed_variant.closed_trades, ev["symbol"], ev["closed_at"])
        if nxt is None or nxt.get("pnl") is None:
            no_reentry += 1
            continue
        if nxt["side"] == ev["orig_side"]:
            same_dir_outcomes.append(nxt["pnl"])
        else:
            opp_dir_outcomes.append(nxt["pnl"])

    print(f"\n=== FOLLOW-UP: what direction does the NEXT trade take after a reversal-close? ===")
    print(f"Reversal-closes with no subsequent trade on that symbol in the window: {no_reentry}")
    if same_dir_outcomes:
        wins = sum(1 for p in same_dir_outcomes if p > 0)
        print(f"Re-entered SAME direction as the original (chop/whipsaw signature): {len(same_dir_outcomes)} times, "
              f"{wins}/{len(same_dir_outcomes)} wins, avg P&L ${sum(same_dir_outcomes)/len(same_dir_outcomes):.2f}, "
              f"total ${sum(same_dir_outcomes):.2f}")
    else:
        print("Re-entered SAME direction as the original: 0 times")
    if opp_dir_outcomes:
        wins = sum(1 for p in opp_dir_outcomes if p > 0)
        print(f"Re-entered NEW (reversal) direction: {len(opp_dir_outcomes)} times, "
              f"{wins}/{len(opp_dir_outcomes)} wins, avg P&L ${sum(opp_dir_outcomes)/len(opp_dir_outcomes):.2f}, "
              f"total ${sum(opp_dir_outcomes):.2f}")
    else:
        print("Re-entered NEW (reversal) direction: 0 times")


if __name__ == "__main__":
    asyncio.run(main())
