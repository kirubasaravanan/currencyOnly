"""Read-only: builds the full FundedNext-planning picture against our own
$10,000 paper baseline -- net profit, max drawdown (%, and $ on $10k),
worst/best single IST-calendar-day P&L, and daily/weekly earning pace --
for majors-only and all-17 (run directly), plus majors+top3/top5 and
top-15 (constructed by summing real per-pair results from the all-17 run,
clearly approximate for drawdown since correlation-blocking and the
5-open-trade cap behave differently with fewer candidate pairs).
"""
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from config import PAIRS, MAJORS
from engine.backtester import run_backtest

IST_OFFSET = timedelta(hours=5, minutes=30)
STARTING_BALANCE = 10_000.0


def ist_date(dt_str: str) -> str:
    dt = datetime.fromisoformat(dt_str)
    return (dt + IST_OFFSET).date().isoformat()


def daily_pnl_stats(trades):
    by_day = defaultdict(float)
    for t in trades:
        by_day[ist_date(t["closed_at"])] += t["pnl"]
    if not by_day:
        return {"worst_day": 0.0, "best_day": 0.0, "days_traded": 0}
    return {
        "worst_day": round(min(by_day.values()), 2),
        "best_day": round(max(by_day.values()), 2),
        "days_traded": len(by_day),
        "avg_day": round(sum(by_day.values()) / len(by_day), 2),
    }


def summarize(label, trades, drawdown_pct=None):
    net = sum(t["pnl"] for t in trades)
    gross = sum(t.get("pnl_gross", t["pnl"]) for t in trades)
    commission = sum(t.get("commission_paid", 0) for t in trades)
    d = daily_pnl_stats(trades)
    print(f"\n=== {label} ===")
    print(f"trades={len(trades)} gross={gross:.2f} commission={commission:.2f} net={net:.2f}")
    print(f"days_traded={d['days_traded']} avg_day=${d.get('avg_day', 0):.2f} "
          f"worst_day=${d['worst_day']:.2f} best_day=${d['best_day']:.2f}")
    if drawdown_pct is not None:
        print(f"max_drawdown={drawdown_pct:.2f}% (${drawdown_pct/100*STARTING_BALANCE:.2f} on $10k)")
    if net > 0:
        per_day_pace = net / max(d['days_traded'], 1)
        for target in (1500, 2000, 3000):
            days_needed = target / per_day_pace if per_day_pace > 0 else float("inf")
            print(f"  pace to ${target}: ~{days_needed:.0f} trading days (~{days_needed/5:.1f} weeks at 5 trading days/week)")
    return {"trades": trades, "net": net, "gross": gross, "commission": commission, **d}


async def main():
    all17 = await run_backtest(symbols=PAIRS, days=60)
    all17_trades = all17["closed_trades"]
    summarize("ALL 17 PAIRS", all17_trades, all17["stats"]["drawdown_pct"])

    majors = await run_backtest(symbols=MAJORS, days=60)
    majors_trades = majors["closed_trades"]
    summarize("MAJORS ONLY (7 pairs)", majors_trades, majors["stats"]["drawdown_pct"])

    # Rank crosses (non-majors) by net P&L from the all-17 run, for
    # majors+topN construction. NOT a permanent selection -- see the
    # overfitting caveat already discussed this session.
    by_symbol_net = defaultdict(float)
    for t in all17_trades:
        by_symbol_net[t["symbol"]] += t["pnl"]
    crosses_ranked = sorted(
        [(s, p) for s, p in by_symbol_net.items() if s not in MAJORS],
        key=lambda kv: kv[1], reverse=True,
    )
    print("\n=== CROSSES RANKED BY NET P&L (this run only) ===")
    for s, p in crosses_ranked:
        print(f"  {s:8s} ${p:.2f}")

    top3_crosses = [s for s, _ in crosses_ranked[:3]]
    top5_crosses = [s for s, _ in crosses_ranked[:5]]
    worst2 = [s for s, _ in crosses_ranked[-2:]]
    top15_symbols = [s for s in PAIRS if s not in worst2]

    summarize(f"MAJORS + TOP 3 CROSSES ({top3_crosses})",
              [t for t in all17_trades if t["symbol"] in MAJORS + top3_crosses])
    summarize(f"MAJORS + TOP 5 CROSSES ({top5_crosses})",
              [t for t in all17_trades if t["symbol"] in MAJORS + top5_crosses])
    summarize(f"TOP 15 (dropping {worst2})",
              [t for t in all17_trades if t["symbol"] in top15_symbols])


if __name__ == "__main__":
    asyncio.run(main())
