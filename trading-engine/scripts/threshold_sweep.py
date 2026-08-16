"""One-off: same 17-pair backtest at three confidence thresholds, with the
Layer-1 trend-filter fix applied, to show the frequency-vs-quality
tradeoff so the threshold can be a deliberate choice, not a guess."""
import asyncio
import json
import sys

sys.path.insert(0, ".")

import config
from config import PAIRS
from engine.backtester import run_backtest


async def main():
    for threshold in (0.65, 0.55, 0.45):
        config.state.confidence_threshold = threshold
        result = await run_backtest(symbols=PAIRS, days=30)
        stats = result["stats"]
        print(f"\n=== threshold={threshold} ===")
        print(f"trades={stats['total_trades']} win_rate={stats['win_rate']} "
              f"profit_factor={stats['profit_factor']} pnl_gross={stats['pnl_gross']} "
              f"commission={stats['commission_paid']} pnl_net={stats['pnl_net']} "
              f"drawdown={stats['drawdown_pct']}")


if __name__ == "__main__":
    asyncio.run(main())
