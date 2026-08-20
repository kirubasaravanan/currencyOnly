"""One-off, read-only research script -- NOT part of the live engine.

Answers the user's question (2026-08-20): their own trading experience
suggests EURGBP specifically shouldn't be traded (a commonly-cited "chop"
pair among discretionary FX traders, since EUR/GBP policy cycles are
closely correlated -- less independent directional drive than most other
crosses). Checks whether the real 60-day backtest actually supports
dropping this one pair, isolating its own standalone stats rather than
relying on today's small live sample (today alone showed EURGBP as the
day's weakest open trade, but one day isn't enough to decide a permanent
pair-list change).
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine.backtester import run_backtest  # noqa: E402
from config import PAIRS  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60


async def main() -> None:
    print(f"Running {DAYS}d backtest across all {len(PAIRS)} pairs...")
    result = await run_backtest(days=DAYS)
    if "error" in result:
        print(result)
        return
    closed = result["closed_trades"]

    by_symbol = {}
    for t in closed:
        sym = t["symbol"]
        row = by_symbol.setdefault(sym, {"n": 0, "wins": 0, "net": 0.0, "gross_profit": 0.0, "gross_loss": 0.0})
        row["n"] += 1
        pnl = t.get("pnl", 0.0)
        row["net"] += pnl
        if pnl > 0:
            row["wins"] += 1
            row["gross_profit"] += pnl
        elif pnl < 0:
            row["gross_loss"] += -pnl

    print(f"\n=== PER-PAIR RESULTS ({DAYS}d, {len(closed)} total trades) -- ranked worst to best net ===")
    ranked = sorted(by_symbol.items(), key=lambda x: x[1]["net"])
    for sym, row in ranked:
        win_pct = 100 * row["wins"] / row["n"] if row["n"] else 0
        pf = (row["gross_profit"] / row["gross_loss"]) if row["gross_loss"] > 0 else (float("inf") if row["gross_profit"] > 0 else 0.0)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        avg = row["net"] / row["n"] if row["n"] else 0
        marker = "  <-- EURGBP" if sym == "EURGBP" else ""
        print(f"  {sym:8s} n={row['n']:3d}  win%={win_pct:5.1f}%  PF={pf_str:>5s}  net=${row['net']:9.2f}  avg/trade=${avg:6.2f}{marker}")

    print(f"\n=== EURGBP SPECIFICALLY ===")
    eg = by_symbol.get("EURGBP")
    if eg is None:
        print("No EURGBP trades in this window at all.")
    else:
        win_pct = 100 * eg["wins"] / eg["n"]
        pf = (eg["gross_profit"] / eg["gross_loss"]) if eg["gross_loss"] > 0 else (float("inf") if eg["gross_profit"] > 0 else 0.0)
        total_net_all_pairs = sum(r["net"] for r in by_symbol.values())
        print(f"Trades: {eg['n']}  Win rate: {win_pct:.1f}%  Profit factor: {pf:.2f}  Net: ${eg['net']:.2f}  Avg/trade: ${eg['net']/eg['n']:.2f}")
        print(f"\nWhat-if EURGBP were dropped entirely: total across remaining {len(PAIRS)-1} pairs = ${total_net_all_pairs - eg['net']:.2f} (actual with EURGBP: ${total_net_all_pairs:.2f})")
        rank_position = [s for s, _ in ranked].index("EURGBP") + 1
        print(f"EURGBP's rank among all {len(ranked)} traded pairs (1 = worst): {rank_position}")


if __name__ == "__main__":
    asyncio.run(main())
