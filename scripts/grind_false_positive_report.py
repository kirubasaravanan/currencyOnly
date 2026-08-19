"""One-off, read-only research script -- NOT part of the live engine.

Directly answers the user's specific blocker (2026-08-19): entries here
are often taken near RSI 70/30 on the 5m chart, so SOME adverse pullback
right after entry before the move continues is a normal, expected feature
of this entry style -- not necessarily a sign the trade is bad. That makes
a simple time-based "slow grind vs fast grind" distinction undecidable by
reasoning alone (a real continuation trade and a real grind can look
identical for the first hour). So: don't guess a duration cutoff --
measure the actual cost, with real data.

This is a pure DETECTION-ONLY pass (GrindDetectBroker below never closes a
trade early -- it just tags each trade with whether it EVER matched the
grind-exit condition -- adverse_pct>=GRIND_ADVERSE_PCT_THRESHOLD AND
peak_pct<=GRIND_PEAK_PCT_THRESHOLD, held for 2 consecutive 15m checks --
at any point during its real, unmodified life). The trade then runs to
its ACTUAL real close exactly as the standard backtester would produce.
Reports:
  - Of trades that would have triggered grind-exit, how many actually
    recovered and closed as a real WIN anyway (the false-positive cost --
    profit the rule would have given up if deployed)?
  - Of trades that would have triggered, how many closed as a real LOSS,
    and how much WORSE did they get after the trigger point vs before (the
    benefit -- loss the rule would actually have avoided)?

This is the direct, decidable version of the RSI-pullback question: not
"is grinding slow or fast" but "does cutting at this threshold actually
throw away real winners, and if so how many / how much."
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

# Both overridable via CLI (days, adverse_pct, peak_pct) for threshold-sweep
# testing -- e.g. `python grind_false_positive_report.py 7 40` tests a
# stricter 40% adverse cutoff over the same 7-day window used at 30%, for
# direct comparison.
GRIND_ADVERSE_PCT_THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
GRIND_PEAK_PCT_THRESHOLD = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0


class GrindDetectBroker(backtester.BacktestBroker):
    """Tags trades with whether they'd ever match the grind condition --
    never intervenes, never closes early. Real outcomes are untouched."""

    def mark_to_market(self, prices: Dict[str, float]) -> None:
        for t in self.open_positions:
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
            is_grinding_now = adverse_pct >= GRIND_ADVERSE_PCT_THRESHOLD and peak_pct <= GRIND_PEAK_PCT_THRESHOLD
            if is_grinding_now:
                if t.get("_pending_grind_detect"):
                    if not t.get("would_grind_trigger"):
                        t["would_grind_trigger"] = True
                        # Unrealized $ pnl as of the PREVIOUS bar (this
                        # bar's real pnl isn't computed until super() runs
                        # below) -- close enough to mark "state at the
                        # trigger point" for the savings estimate.
                        t["grind_trigger_pnl_snapshot"] = t.get("pnl", 0.0)
                else:
                    t["_pending_grind_detect"] = True
            else:
                t["_pending_grind_detect"] = False
        super().mark_to_market(prices)


async def run(days: int):
    frames_by_symbol = {}
    for symbol in backtester.PAIRS:
        frames_by_symbol[symbol] = await backtester._fetch_symbol_frames(symbol, days)

    all_times = sorted(set().union(*[
        set(f["15m"].index) for f in frames_by_symbol.values() if not f["15m"].empty
    ])) if frames_by_symbol else []
    if not all_times:
        return {"error": "no_data"}

    broker = GrindDetectBroker()
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

    return {"stats": compute_stats(broker), "closed_trades": broker.closed_trades}


def summarize(closed):
    triggered = [t for t in closed if t.get("would_grind_trigger")]
    not_triggered = [t for t in closed if not t.get("would_grind_trigger")]

    print(f"\n=== ALL TRADES: {len(closed)} total, {len(triggered)} would have matched the grind condition at some point ===")

    trig_winners = [t for t in triggered if t.get("pnl", 0) > 0]
    trig_losers = [t for t in triggered if t.get("pnl", 0) <= 0]

    print(f"\n=== OF THE {len(triggered)} THAT WOULD HAVE TRIGGERED GRIND-EXIT ===")
    if triggered:
        win_rate = 100 * len(trig_winners) / len(triggered)
        print(f"Recovered to a real WIN anyway: {len(trig_winners)}/{len(triggered)} ({win_rate:.1f}%)")
        if trig_winners:
            total_recovered_profit = sum(t["pnl"] for t in trig_winners)
            print(f"  Total profit these trades actually made: ${total_recovered_profit:.2f} -- "
                  f"this is what a hard grind-exit rule would have given up")
        print(f"Stayed a real LOSS: {len(trig_losers)}/{len(triggered)}")
        if trig_losers:
            total_final_loss = sum(t["pnl"] for t in trig_losers)
            total_snapshot_at_trigger = sum(t.get("grind_trigger_pnl_snapshot", 0.0) for t in trig_losers)
            estimated_savings = total_snapshot_at_trigger - total_final_loss  # both negative; snapshot is less negative
            print(f"  Total final loss: ${total_final_loss:.2f}")
            print(f"  Total unrealized pnl AT the trigger point: ${total_snapshot_at_trigger:.2f}")
            print(f"  Estimated $ saved by cutting at the trigger instead of riding to the real close: ${estimated_savings:.2f}")
    else:
        print("None -- the grind condition never matched a single trade in this window.")

    print(f"\n=== NET VERDICT (this threshold, this window) ===")
    if triggered:
        gross_given_up = sum(t["pnl"] for t in trig_winners) if trig_winners else 0.0
        gross_saved = (sum(t.get("grind_trigger_pnl_snapshot", 0.0) for t in trig_losers) - sum(t["pnl"] for t in trig_losers)) if trig_losers else 0.0
        print(f"Profit given up (cutting eventual winners early): ${gross_given_up:.2f}")
        print(f"Loss avoided (cutting eventual losers early):     ${gross_saved:.2f}")
        print(f"Net effect of deploying grind-exit at these thresholds: ${gross_saved - gross_given_up:+.2f}")


async def main():
    print(f"Running {DAYS}d detection-only pass (no trades closed early, real outcomes preserved)...")
    result = await run(DAYS)
    if "error" in result:
        print(result)
        return
    summarize(result["closed_trades"])
    print("\n=== OVERALL (unchanged from real baseline -- detection only) ===")
    stats = result["stats"]
    print(f"Trades: {stats['total_trades']}  Win rate: {stats['win_rate']}%  "
          f"Net P&L: ${stats['pnl_net']:.2f}  Profit factor: {stats['profit_factor']}")


if __name__ == "__main__":
    asyncio.run(main())
