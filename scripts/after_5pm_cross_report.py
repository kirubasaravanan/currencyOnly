"""One-off, read-only research script -- NOT part of the live engine.

Answers the user's question (2026-08-19): the only 2 real cross-pair
(non-major) trades ever opened after 17:00 IST in this app's short real
history were both AUDJPY, both losses -- net -$107.01, vs. +$108.55 for
18 cross-pair trades opened before 17:00 IST. That's only 2 data points
though, too thin to trust as a real pattern rather than AUDJPY-specific
noise. This reuses the unmodified 60-day backtest (engine.backtester.
run_backtest, the same real numbers used everywhere else this session) to
get real statistical power on the same question: do cross-pair trades
opened after 17:00 IST actually underperform, and is cutting them off at
5PM (the user's stated plan -- majors keep trading after 5PM, crosses
don't) actually a net improvement?
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trading-engine"))

from engine.backtester import run_backtest  # noqa: E402
from config import MAJORS  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
IST = timedelta(hours=5, minutes=30)
CUTOFF_HOUR = 17


def ist_dt(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str) + IST


async def main() -> None:
    print(f"Running {DAYS}d backtest...")
    result = await run_backtest(days=DAYS)
    if "error" in result:
        print(result)
        return
    closed = [t for t in result["closed_trades"] if t.get("opened_at")]

    majors_set = set(MAJORS)
    crosses_after = []
    crosses_before = []
    majors_after = []
    majors_before = []

    for t in closed:
        is_cross = t["symbol"] not in majors_set
        is_after = ist_dt(t["opened_at"]).hour >= CUTOFF_HOUR
        bucket = (crosses_after if is_cross and is_after else
                  crosses_before if is_cross else
                  majors_after if is_after else majors_before)
        bucket.append(t)

    def summarize(label: str, trades: list) -> float:
        if not trades:
            print(f"{label}: 0 trades")
            return 0.0
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        net = sum(t.get("pnl", 0) for t in trades)
        print(f"{label}: n={len(trades)}  {wins}W-{len(trades)-wins}L  win%={100*wins/len(trades):.1f}%  net=${net:.2f}  avg/trade=${net/len(trades):.2f}")
        return net

    print(f"\n=== {DAYS}d BACKTEST: cross-pair (non-major) trades, split at {CUTOFF_HOUR}:00 IST ===")
    after_net = summarize("Cross-pair, opened AFTER 17:00 IST", crosses_after)
    before_net = summarize("Cross-pair, opened BEFORE 17:00 IST", crosses_before)

    print(f"\n=== For context: majors, same split ===")
    summarize("Majors, opened AFTER 17:00 IST", majors_after)
    summarize("Majors, opened BEFORE 17:00 IST", majors_before)

    all_net = sum(t.get("pnl", 0) for t in closed)
    print(f"\n=== WHAT-IF: cut cross-pair entries off at 17:00 IST (majors keep trading after) ===")
    print(f"Actual total ({len(closed)} trades): ${all_net:.2f}")
    print(f"Total WITHOUT the {len(crosses_after)} after-5PM cross trades: ${all_net - after_net:.2f}")
    print(f"Difference: ${-after_net:+.2f}")

    print(f"\n=== By symbol, cross-pair after-17:00 trades ===")
    by_sym: dict = {}
    for t in crosses_after:
        sym = t["symbol"]
        row = by_sym.setdefault(sym, {"n": 0, "wins": 0, "net": 0.0})
        row["n"] += 1
        row["net"] += t.get("pnl", 0)
        if t.get("pnl", 0) > 0:
            row["wins"] += 1
    for sym, row in sorted(by_sym.items(), key=lambda x: x[1]["net"]):
        print(f"  {sym:8s} n={row['n']:3d}  wins={row['wins']:3d}  net=${row['net']:.2f}  avg=${row['net']/row['n']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
