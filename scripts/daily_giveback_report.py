"""One-off, read-only research script -- NOT part of the live engine.

Answers: across many real trading days, how far does cumulative daily P&L
typically pull back from its intraday peak before the day ends? Used to
calibrate an evidence-based threshold for a possible future "daily
give-back" circuit breaker (explicit user request, 2026-08-18) -- rather
than guessing a number, look at the actual distribution of peak-to-close
pullbacks across a real 60-day backtest.

Reuses backtester.py's exact, unmodified run_backtest() (same code path
the live engine runs, current REQUIRE_CONFLUENCE_GATE=True setting) --
this script only POST-PROCESSES the resulting closed_trades list, it
doesn't touch the walk-forward loop itself. For each IST calendar day,
reconstructs the running cumulative net P&L from the day's trade-close
sequence (chronological), and records that day's peak and its give-back
(peak minus end-of-day total). Only realized (closed-trade) P&L is used,
not continuous mark-to-market of open positions -- a reasonable
simplification since a "protect what's already realized" circuit breaker
only cares about realized swings anyway.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine.backtester import run_backtest  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
IST = timedelta(hours=5, minutes=30)


def ist_date(iso_str: str):
    return (datetime.fromisoformat(iso_str) + IST).date()


async def main() -> None:
    result = await run_backtest(days=DAYS)
    if "error" in result:
        print(result)
        return

    closed = result["closed_trades"]
    by_day = defaultdict(list)
    for t in closed:
        if not t.get("closed_at"):
            continue
        by_day[ist_date(t["closed_at"])].append(t)

    rows = []
    for day, trades in sorted(by_day.items()):
        trades_sorted = sorted(trades, key=lambda t: t["closed_at"])
        running = 0.0
        peak = 0.0
        for t in trades_sorted:
            running += t.get("pnl", 0.0)
            peak = max(peak, running)
        final = running
        giveback = round(peak - final, 2) if peak > 0 else 0.0
        rows.append((day, len(trades), round(peak, 2), round(final, 2), giveback))

    print(f"{'DATE':<12}{'TRADES':>8}{'PEAK':>10}{'FINAL':>10}{'GIVEBACK':>10}")
    for day, n, peak, final, giveback in rows:
        print(f"{str(day):<12}{n:>8}{peak:>10.2f}{final:>10.2f}{giveback:>10.2f}")

    positive_peak_days = [r for r in rows if r[2] > 0]
    givebacks = sorted(r[4] for r in positive_peak_days)
    finals = [r[3] for r in rows]

    print(f"\n=== SUMMARY ({len(rows)} days, {len(positive_peak_days)} with a positive intraday peak) ===")
    if givebacks:
        n = len(givebacks)
        print(f"Give-back distribution (only days with a positive peak):")
        print(f"  min:    ${givebacks[0]:.2f}")
        print(f"  median: ${givebacks[n // 2]:.2f}")
        print(f"  p75:    ${givebacks[int(n * 0.75)]:.2f}")
        print(f"  p90:    ${givebacks[int(n * 0.90)] if n > 10 else givebacks[-1]:.2f}")
        print(f"  max:    ${givebacks[-1]:.2f}")
        print(f"  mean:   ${sum(givebacks)/n:.2f}")
    print(f"\nAvg final daily net P&L (all days): ${sum(finals)/len(finals):.2f}")
    print(f"Total net P&L over {DAYS} days: ${sum(finals):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
