"""One-off, read-only research script -- NOT part of the live engine.

Discovery backtest for the other two trial pairs (EURAUD, EURNZD), added
2026-08-21 alongside EURJPY/NZDJPY/CADJPY -- see
new_pairs_discovery_backtest.py's docstring for the full rationale
(European/risk-on vs commodity/Asian-Pacific, different correlation
groups, not the same-group correlation that made EURGBP chop).
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
NEW_PAIRS = ["EURAUD", "EURNZD"]


def ist_hour(iso_ts: str) -> int:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + IST).hour


async def main() -> None:
    print(f"Running {DAYS}d backtest for {NEW_PAIRS} (full 24h gate, discovery mode)...")
    result = await run_backtest(symbols=NEW_PAIRS, days=DAYS)
    if "error" in result:
        print(result)
        return

    all_trades = result["closed_trades"]
    print(f"\nTotal trades across both pairs: {len(all_trades)}")

    for pair in NEW_PAIRS:
        trades = [t for t in all_trades if t["symbol"] == pair]
        total_net = sum(t.get("pnl", 0.0) for t in trades)
        wins = sum(1 for t in trades if t.get("pnl", 0.0) > 0)
        gp = sum(t["pnl"] for t in trades if t.get("pnl", 0.0) > 0)
        gl = sum(-t["pnl"] for t in trades if t.get("pnl", 0.0) < 0)
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        win_pct = 100 * wins / len(trades) if trades else 0

        print(f"\n{'=' * 70}\n{pair}")
        print(f"n={len(trades)}  win%={win_pct:.1f}%  PF={pf_str}  net=${total_net:.2f}")
        if not trades:
            print("No trades at all -- can't bucket by hour.")
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

        print(f"{'Hour':>6s} {'n':>4s} {'win%':>6s} {'PF':>6s} {'net':>10s}")
        for h in range(24):
            row = by_hour.get(h)
            if row is None:
                continue
            wr = 100 * row["wins"] / row["n"] if row["n"] else 0
            hpf = (row["gp"] / row["gl"]) if row["gl"] > 0 else (float("inf") if row["gp"] > 0 else 0.0)
            hpf_str = "inf" if hpf == float("inf") else f"{hpf:.2f}"
            print(f"{h:02d}:00 {row['n']:>4d} {wr:>5.1f}% {hpf_str:>6s} ${row['net']:>9.2f}")


if __name__ == "__main__":
    asyncio.run(main())
