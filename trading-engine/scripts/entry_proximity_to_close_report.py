"""Read-only analysis: for every closed trade, how many minutes before its
pair's own session-close deadline did it open? Buckets P&L by that
proximity to see whether late-opened trades are systematically worse."""
import asyncio
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from config import PAIRS, PAIR_CALIBRATION
from engine.backtester import run_backtest

IST_OFFSET = timedelta(hours=5, minutes=30)


def ist_minutes_of_day(dt: datetime) -> int:
    ist = dt + IST_OFFSET
    return ist.hour * 60 + ist.minute


def bucket(mins_before_close: float) -> str:
    if mins_before_close < 15:
        return "<15 min"
    if mins_before_close < 30:
        return "15-30 min"
    if mins_before_close < 45:
        return "30-45 min"
    if mins_before_close < 60:
        return "45-60 min"
    return ">60 min"


async def main():
    result = await run_backtest(symbols=PAIRS, days=30)
    trades = result["closed_trades"]
    print(f"total trades: {len(trades)}\n")

    buckets = {}
    for t in trades:
        calib = PAIR_CALIBRATION[t["symbol"]]
        last_window_end = max(end for _, end in calib.session_windows_ist)
        opened = datetime.fromisoformat(t["opened_at"])
        opened_ist_min = ist_minutes_of_day(opened)
        mins_before_close = last_window_end - opened_ist_min
        if mins_before_close < 0:
            continue  # opened during a different window than the day's last one; not relevant to THIS question
        b = bucket(mins_before_close)
        row = buckets.setdefault(b, {"n": 0, "wins": 0, "net": 0.0, "session_close_exits": 0})
        row["n"] += 1
        if t["pnl"] > 0:
            row["wins"] += 1
        row["net"] += t["pnl"]
        if t["reason"] == "session_close":
            row["session_close_exits"] += 1

    order = ["<15 min", "15-30 min", "30-45 min", "45-60 min", ">60 min"]
    print(f"{'bucket':12s} {'n':>4s} {'win%':>6s} {'avg_net':>10s} {'total_net':>10s} {'closed_by_session_end':>22s}")
    for b in order:
        row = buckets.get(b)
        if not row:
            continue
        win_pct = 100 * row["wins"] / row["n"]
        avg_net = row["net"] / row["n"]
        pct_session_closed = 100 * row["session_close_exits"] / row["n"]
        print(f"{b:12s} {row['n']:4d} {win_pct:5.1f}% {avg_net:10.2f} {row['net']:10.2f} {pct_session_closed:21.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
