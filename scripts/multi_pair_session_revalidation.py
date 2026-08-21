"""One-off, read-only research script -- NOT part of the live engine.

Follow-up to USDCHF/AUDJPY revalidation (2026-08-21): a theoretical review
against session_dominance.py's own currency-activity mapping flagged
several more pairs whose configured windows may sit largely in
"quiet" (neither currency dominant) time. Explicit user instruction:
run the backtest and observe -- do NOT apply any fix yet, this is a
monitoring/evidence-gathering pass only.

Pairs checked, all flagged by the theoretical review:
  USDCAD  -- current window almost entirely outside Overlap/NY (the only
             sessions where either USD or CAD is dominant) -- worst
             theoretical mismatch found, bigger than USDCHF's original.
  GBPCAD, EURCAD, NZDCAD -- each has a multi-hour block sitting in a
             session where neither currency is dominant.
  EURGBP  -- structural: EUR and GBP share almost identical dominant
             sessions, so this pair never gets a single-dominant "clean"
             window at all, only "quiet" or "battle" (both dominant).
  AUDUSD, NZDUSD, AUDCAD -- milder, partial quiet-hour overlap mixed into
             otherwise reasonable windows.

Method: widens ALL of these pairs' session gates to the full 24h
simultaneously (each pair's own gate only checks its own symbol, so this
doesn't cross-contaminate signal generation between pairs -- correlation/
exposure checks behave exactly as they would live), runs ONE 60-day
backtest, then buckets each pair's own trades by entry hour (IST)
separately. Far cheaper than one backtest run per pair.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine.backtester import run_backtest  # noqa: E402
from config import PAIR_CALIBRATION  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
IST = timedelta(hours=5, minutes=30)

FLAGGED_PAIRS = ["USDCAD", "GBPCAD", "EURCAD", "NZDCAD", "EURGBP", "AUDUSD", "NZDUSD", "AUDCAD"]

ORIGINAL_WINDOWS = {p: PAIR_CALIBRATION[p].session_windows_ist for p in FLAGGED_PAIRS}

for p in FLAGGED_PAIRS:
    PAIR_CALIBRATION[p] = dataclasses.replace(PAIR_CALIBRATION[p], session_windows_ist=((0, 1440),))


def ist_hour(iso_ts: str) -> int:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + IST).hour


def in_original_windows(pair: str, hour: int) -> bool:
    minutes = hour * 60
    return any(start <= minutes < end for start, end in ORIGINAL_WINDOWS[pair])


async def main() -> None:
    print(f"Running {DAYS}d backtest with {len(FLAGGED_PAIRS)} pairs' session gates fully opened...")
    result = await run_backtest(days=DAYS)
    if "error" in result:
        print(result)
        return

    all_trades = result["closed_trades"]

    for pair in FLAGGED_PAIRS:
        trades = [t for t in all_trades if t["symbol"] == pair]
        print(f"\n{'=' * 70}\n{pair}  (current window: {ORIGINAL_WINDOWS[pair]})")
        print(f"Total trades across all 24 hours: {len(trades)}")
        if not trades:
            print("No trades at all in this window -- can't bucket.")
            continue

        by_hour = defaultdict(lambda: {"n": 0, "wins": 0, "net": 0.0, "gp": 0.0, "gl": 0.0})
        for t in trades:
            h = ist_hour(t["opened_at"])
            row = by_hour[h]
            pnl = t.get("pnl", 0.0)
            row["n"] += 1
            row["net"] += pnl
            if pnl > 0:
                row["wins"] += 1
                row["gp"] += pnl
            elif pnl < 0:
                row["gl"] += -pnl

        print(f"{'Hour':>6s} {'n':>4s} {'win%':>6s} {'PF':>6s} {'net':>10s}  in-current-window?")
        total_in_window = 0.0
        total_out_window = 0.0
        for h in range(24):
            row = by_hour.get(h)
            marker = "yes" if in_original_windows(pair, h) else "no"
            if row is None:
                continue
            wr = 100 * row["wins"] / row["n"] if row["n"] else 0
            pf = (row["gp"] / row["gl"]) if row["gl"] > 0 else (float("inf") if row["gp"] > 0 else 0.0)
            pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
            print(f"{h:02d}:00 {row['n']:>4d} {wr:>5.1f}% {pf_str:>6s} ${row['net']:>9.2f}  {marker}")
            if in_original_windows(pair, h):
                total_in_window += row["net"]
            else:
                total_out_window += row["net"]

        print(f"Current window net: ${total_in_window:.2f}  |  Outside-window net: ${total_out_window:.2f}  |  Whole-day: ${total_in_window + total_out_window:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
