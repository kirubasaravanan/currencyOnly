"""One-off, read-only research script -- NOT part of the live engine.

Answers the user's hypothesis (2026-08-19): does a HIGH confidence score
actually predict a WORSE trade, because reaching high confidence requires
enough confluence factors to already be confirmed that the move is often
already extended by the time entry happens (same underlying concern as
the RSI-70/30 entry-pullback discussion)? The 39-trade real sample showed
no clean pattern but is too small to trust -- this reuses the unmodified
backtester (engine.backtester.run_backtest, same real numbers used
everywhere else this session) to get a much larger sample.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine.backtester import run_backtest  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60

BUCKETS = [(0.0, 0.75, "low (<0.75)"), (0.75, 0.90, "mid (0.75-0.90)"), (0.90, 1.01, "high (>=0.90)")]


def bucket_for(conf: float) -> str:
    for lo, hi, label in BUCKETS:
        if lo <= conf < hi:
            return label
    return "other"


async def main() -> None:
    print(f"Running {DAYS}d backtest...")
    result = await run_backtest(days=DAYS)
    if "error" in result:
        print(result)
        return
    closed = result["closed_trades"]

    by_bucket = defaultdict(lambda: {"n": 0, "wins": 0, "net": 0.0})
    for t in closed:
        b = bucket_for(t.get("confidence", 0))
        by_bucket[b]["n"] += 1
        by_bucket[b]["net"] += t.get("pnl", 0.0)
        if t.get("pnl", 0.0) > 0:
            by_bucket[b]["wins"] += 1

    print(f"\n=== {DAYS}d BACKTEST: confidence vs outcome ({len(closed)} trades) ===")
    for lo, hi, label in BUCKETS:
        row = by_bucket.get(label)
        if not row or row["n"] == 0:
            print(f"  {label:20s} n=0")
            continue
        wr = 100 * row["wins"] / row["n"]
        avg_pnl = row["net"] / row["n"]
        print(f"  {label:20s} n={row['n']:4d}  win%={wr:5.1f}%  net=${row['net']:9.2f}  avg/trade=${avg_pnl:6.2f}")

    confs = [t.get("confidence", 0) for t in closed]
    pnls = [t.get("pnl", 0) for t in closed]
    n = len(confs)
    mean_c = sum(confs) / n
    mean_p = sum(pnls) / n
    cov = sum((c - mean_c) * (p - mean_p) for c, p in zip(confs, pnls)) / n
    std_c = (sum((c - mean_c) ** 2 for c in confs) / n) ** 0.5
    std_p = (sum((p - mean_p) ** 2 for p in pnls) / n) ** 0.5
    corr = cov / (std_c * std_p) if std_c > 0 and std_p > 0 else 0
    print(f"\nPearson correlation (confidence vs pnl), n={n}: {corr:.3f}")
    print("(0 = no relationship, positive = higher confidence -> more profit, negative = higher confidence -> less profit)")

    # Also break down by whether the trade ever showed favorable excursion
    # before resolving -- directly tests "does high confidence correlate
    # with entries that are already extended (little room left to run)?"
    for lo, hi, label in BUCKETS:
        grp = [t for t in closed if lo <= t.get("confidence", 0) < hi]
        if not grp:
            continue
        peak_favs = [t.get("peak_favorable_move", 0.0) for t in grp]
        zero_fav = sum(1 for p in peak_favs if p <= 0)
        print(f"  {label:20s}: {zero_fav}/{len(grp)} trades NEVER showed favorable movement at all")


if __name__ == "__main__":
    asyncio.run(main())
