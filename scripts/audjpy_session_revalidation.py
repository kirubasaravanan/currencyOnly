"""One-off, read-only research script -- NOT part of the live engine.

Follow-up to the USDCHF revalidation (2026-08-21): AUDJPY is the worst
performing pair in the live paper track record so far (-$77.76 net,
PF 0.27, n=5) -- checking whether its ported V109 session windows
(05:30-10:00, 17:30-20:00 IST) actually line up with AUD/JPY's real
active hours (both are Asian-session currencies; the second window falls
in London hours where neither is dominant, per session_dominance.py's
own mapping).

Method: strips AUDJPY's session gate entirely (full 24h window) so every
hour gets a fair chance to produce a signal, runs a 60-day backtest, then
buckets the resulting trades by entry hour (IST) to see where the pair's
actual edge concentrates -- compared against the two ported windows.
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

ORIGINAL_WINDOWS = PAIR_CALIBRATION["AUDJPY"].session_windows_ist

PAIR_CALIBRATION["AUDJPY"] = dataclasses.replace(
    PAIR_CALIBRATION["AUDJPY"], session_windows_ist=((0, 1440),)
)


def ist_hour(iso_ts: str) -> int:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + IST).hour


async def main() -> None:
    print(f"Running {DAYS}d backtest with AUDJPY's session gate fully opened (0000-2400 IST)...")
    result = await run_backtest(days=DAYS)
    if "error" in result:
        print(result)
        return

    trades = [t for t in result["closed_trades"] if t["symbol"] == "AUDJPY"]
    print(f"\nTotal AUDJPY trades across all 24 hours: {len(trades)}")
    if not trades:
        print("No AUDJPY trades at all in this window -- can't bucket.")
        return

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

    def in_original_windows(hour: int) -> bool:
        minutes = hour * 60
        return any(start <= minutes < end for start, end in ORIGINAL_WINDOWS)

    print(f"\n=== AUDJPY net P&L by entry hour (IST), {DAYS}d ===")
    print(f"{'Hour':>6s} {'n':>4s} {'win%':>6s} {'PF':>6s} {'net':>10s}  in-current-window?")
    total_in_window = 0.0
    total_out_window = 0.0
    for h in range(24):
        row = by_hour.get(h)
        marker = "yes" if in_original_windows(h) else "no"
        if row is None:
            print(f"{h:02d}:00 {'0':>4s} {'-':>6s} {'-':>6s} {'$0.00':>10s}  {marker}")
            continue
        wr = 100 * row["wins"] / row["n"] if row["n"] else 0
        pf = (row["gp"] / row["gl"]) if row["gl"] > 0 else (float("inf") if row["gp"] > 0 else 0.0)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{h:02d}:00 {row['n']:>4d} {wr:>5.1f}% {pf_str:>6s} ${row['net']:>9.2f}  {marker}")
        if in_original_windows(h):
            total_in_window += row["net"]
        else:
            total_out_window += row["net"]

    print(f"\nCurrent windows (05:30-10:00, 17:30-20:00 IST) net: ${total_in_window:.2f}")
    print(f"All other hours combined net: ${total_out_window:.2f}")
    print(f"Whole-day total: ${total_in_window + total_out_window:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
