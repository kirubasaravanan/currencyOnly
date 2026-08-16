"""Read-only analysis (no config/logic changes): majors-only drawdown, and
lot-size distribution (min/max/avg) per pair and overall, from the current
gating logic. Also dumps the full trade list for one example pair.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from config import PAIRS
from engine.backtester import run_backtest

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]


async def main():
    # Full 17-pair run: lot-size stats + one pair's full trade list
    full = await run_backtest(symbols=PAIRS, days=60)
    print(f"=== FULL 17-PAIR: drawdown_pct={full['stats']['drawdown_pct']} ===\n")

    by_pair_lots = {}
    for t in full["closed_trades"]:
        by_pair_lots.setdefault(t["symbol"], []).append(t["original_lots"])

    print("=== LOT SIZE per pair (min / max / avg / n) ===")
    all_lots = []
    for sym in sorted(by_pair_lots):
        lots = by_pair_lots[sym]
        all_lots.extend(lots)
        print(f"  {sym:8s} min={min(lots):.2f} max={max(lots):.2f} avg={sum(lots)/len(lots):.3f} n={len(lots)}")
    print(f"\nOVERALL: min={min(all_lots):.2f} max={max(all_lots):.2f} avg={sum(all_lots)/len(all_lots):.3f} n={len(all_lots)}")
    print(f"Configured LOT_BOUNDS (all pairs): (0.01, 0.50)")

    example = "EURUSD"
    print(f"\n=== FULL TRADE LIST: {example} ===")
    for t in [t for t in full["closed_trades"] if t["symbol"] == example]:
        print(f"  id={t['id']:3d} side={t['side']:8s} lots={t['original_lots']:.2f} "
              f"entry={t['entry_price']:.5f} exit={t.get('exit_price', 0):.5f} "
              f"opened={t['opened_at'][:16]} closed={t['closed_at'][:16]} "
              f"reason={t['reason']:18s} gross={t.get('pnl_gross', 0):8.2f} "
              f"commission={t.get('commission_paid', 0):6.2f} net={t['pnl']:8.2f}")

    # Majors-only run: its own drawdown
    majors = await run_backtest(symbols=MAJORS, days=60)
    print(f"\n=== MAJORS-ONLY (7 pairs): drawdown_pct={majors['stats']['drawdown_pct']} "
          f"trades={majors['stats']['total_trades']} net={majors['stats']['pnl_net']} "
          f"peak_equity={majors['stats']['peak_equity']} equity={majors['stats']['equity']} ===")


if __name__ == "__main__":
    asyncio.run(main())
