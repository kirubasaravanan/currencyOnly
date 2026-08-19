"""One-off, read-only research script -- NOT part of the live engine.

Answers the user's question (2026-08-19): the sister Forex/Forex app has a
live "grind exit" rule (trading-engine/engine/trade_manager.py, flipped
live 2026-08-01, found there after 81% of a 128-loss sample matched this
exact profile) that catches a trade that's moved steadily against the
position with essentially no favorable excursion at all -- exactly the
GBPAUD/GBPNZD pattern seen live in currencyOnly today. The user asked to
port ONLY this one rule (not the sister app's whole trade_manager.py) and
check whether it would meaningfully reduce losses here, rather than
assuming the sister app's own thresholds (which themselves were only
validated on a same-day PROXY backtest over there, never a real retest --
see prior-session notes) transfer directly to a different app with
different pairs, sizing, and commission model.

Rule (ported as-is, same starting threshold values as the sister app):
  adverse_pct = (how far current price has moved AGAINST the position) / original_SL_distance * 100
  peak_pct    = (best favorable move ever reached)                     / original_SL_distance * 100
  grinding if adverse_pct >= GRIND_ADVERSE_PCT_THRESHOLD AND peak_pct <= GRIND_PEAK_PCT_THRESHOLD
  requires the SAME condition on two consecutive checks before closing --
  same noise-filtering pattern the sister app itself uses -- one bad tick
  shouldn't cut a trade that's about to turn.

currencyOnly's paper_broker.py already tracks the two fields this rule
needs on every trade (`sl_dist` = original SL distance at open, never
mutated afterward; `peak_favorable_move`, updated every mark_to_market()
call) -- no new tracking needed, just the check itself.

Compares baseline (engine.backtester.run_backtest(), unmodified, the exact
same numbers used everywhere else this session) against a GrindBroker
variant that adds ONLY this one check on top of the existing SL/TP/BE/
trailing logic -- signal engine, sizing, and every other exit path is
untouched. Also directly tests the user's specific claim ("at least 50%
loss reduction") against the biggest real losing trades from the actual
backtest data, not a guess.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Dict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine import backtester  # noqa: E402
from engine import risk, correlation, trade_manager, currency_strength  # noqa: E402
from engine.entry import entry_signal  # noqa: E402
from engine.analytics import compute_stats  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60

# Ported starting values from the sister Forex/Forex app's own live
# GRIND_ADVERSE_PCT_THRESHOLD/GRIND_PEAK_PCT_THRESHOLD -- NOT yet validated
# against this app's own data, which is exactly what this script checks.
# Both overridable via CLI (days, adverse_pct, peak_pct) for threshold-sweep
# testing against the same window used at the default 30%/20%.
GRIND_ADVERSE_PCT_THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
GRIND_PEAK_PCT_THRESHOLD = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0


class GrindBroker(backtester.BacktestBroker):
    def mark_to_market(self, prices: Dict[str, float]) -> None:
        for t in list(self.open_positions):
            price = prices.get(t["symbol"])
            if price is None:
                continue
            sl_distance = t.get("sl_dist", 0.0)
            if sl_distance <= 0:
                continue
            direction = 1 if t["side"] == "BULLISH" else -1
            favorable_move = direction * (price - t["entry_price"])
            adverse_move = max(0.0, -favorable_move)
            adverse_pct = adverse_move / sl_distance * 100.0
            peak_pct = max(0.0, t.get("peak_favorable_move", 0.0)) / sl_distance * 100.0
            is_grinding = adverse_pct >= GRIND_ADVERSE_PCT_THRESHOLD and peak_pct <= GRIND_PEAK_PCT_THRESHOLD
            if is_grinding:
                if t.get("pending_grind_exit"):
                    self.close_trade(t["id"], price, "grind_exit", prices)
                    continue
                t["pending_grind_exit"] = True
            else:
                t["pending_grind_exit"] = False
        super().mark_to_market(prices)


async def _simulate_with_grind(days: int):
    frames_by_symbol = {}
    for symbol in backtester.PAIRS:
        frames_by_symbol[symbol] = await backtester._fetch_symbol_frames(symbol, days)

    all_times = sorted(set().union(*[
        set(f["15m"].index) for f in frames_by_symbol.values() if not f["15m"].empty
    ])) if frames_by_symbol else []
    if not all_times:
        return None

    broker = GrindBroker()
    broker.reset()

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

    return broker


async def main() -> None:
    print(f"Running baseline (unmodified) {DAYS}d backtest...")
    baseline = await backtester.run_backtest(days=DAYS)
    if "error" in baseline:
        print(baseline)
        return
    b_stats = baseline["stats"]

    print(f"Running grind-exit variant ({DAYS}d, adverse>={GRIND_ADVERSE_PCT_THRESHOLD}% / peak<={GRIND_PEAK_PCT_THRESHOLD}% of original SL distance)...")
    grind_broker = await _simulate_with_grind(DAYS)
    g_stats = compute_stats(grind_broker)

    grind_exits = [t for t in grind_broker.closed_trades if t.get("reason") == "grind_exit"]

    print(f"\n=== BASELINE ({DAYS}d, unmodified, actual behavior) ===")
    print(f"Trades: {b_stats['total_trades']}  Win rate: {b_stats['win_rate']}%  "
          f"Net P&L: ${b_stats['pnl_net']:.2f}  Profit factor: {b_stats['profit_factor']}  "
          f"Max drawdown: {b_stats['drawdown_pct']}%")

    print(f"\n=== WITH GRIND EXIT ===")
    print(f"Trades: {g_stats['total_trades']}  Win rate: {g_stats['win_rate']}%  "
          f"Net P&L: ${g_stats['pnl_net']:.2f}  Profit factor: {g_stats['profit_factor']}  "
          f"Max drawdown: {g_stats['drawdown_pct']}%")
    print(f"Grind-exit triggers: {len(grind_exits)}")
    if grind_exits:
        pnls = [t["pnl"] for t in grind_exits]
        print(f"  Avg P&L at grind-exit: ${sum(pnls)/len(pnls):.2f}  (min ${min(pnls):.2f}, max ${max(pnls):.2f})")
        losers = [p for p in pnls if p < 0]
        print(f"  {len(losers)}/{len(pnls)} were still losses at exit -- grind exit caps a loss early, it doesn't guarantee a win")

    print(f"\n=== DELTA vs baseline ===")
    print(f"Net P&L: ${g_stats['pnl_net'] - b_stats['pnl_net']:+.2f}")
    print(f"Trade count: {g_stats['total_trades'] - b_stats['total_trades']:+d}")

    # Directly test the user's specific claim ("at least 50% loss reduction")
    # against the biggest REAL losing trades, not a guess. Matched by
    # (symbol, opened_at) since entry logic is identical between the two
    # runs until the first grind-exit intervention causes downstream state
    # (cooldown timing, equity, correlation exposure) to diverge -- trades
    # after that point on the same symbol may not have an exact match, which
    # is expected and reported as such rather than silently skipped.
    baseline_losses = sorted((t for t in baseline["closed_trades"] if t.get("pnl", 0) < 0), key=lambda t: t["pnl"])[:15]
    grind_by_key = {(t["symbol"], t.get("opened_at")): t for t in grind_broker.closed_trades}
    print(f"\n=== BIGGEST 15 BASELINE LOSSES vs. WHAT THE GRIND-EXIT VARIANT DID WITH THE SAME TRADE ===")
    for bt in baseline_losses:
        key = (bt["symbol"], bt.get("opened_at"))
        gt = grind_by_key.get(key)
        if gt is None:
            print(f"{bt['symbol']:8s} baseline ${bt['pnl']:8.2f}  -> no matching trade in grind variant (downstream state had already diverged)")
            continue
        reduction_pct = (1 - gt["pnl"] / bt["pnl"]) * 100 if bt["pnl"] != 0 else 0
        print(f"{bt['symbol']:8s} baseline ${bt['pnl']:8.2f}  -> grind-variant ${gt['pnl']:8.2f} ({gt.get('reason')}) -- loss reduced {reduction_pct:.0f}%")


if __name__ == "__main__":
    asyncio.run(main())
