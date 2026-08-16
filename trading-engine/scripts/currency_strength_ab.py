"""A/B comparison: does the currency_strength Layer-2 factor reduce trade
count, and what does it do to quality? Same 60-day/17-pair window both
times, only this one factor toggled (weight AND denominator both excluded
when off, not just neutralized) -- isolates its effect from the trend-
filter fix and 5m-trigger experiment, which were bundled together in
earlier runs."""
import asyncio
import sys

sys.path.insert(0, ".")

import config
from config import PAIRS
from engine.backtester import run_backtest


async def main():
    for label, flag in (("currency_strength OFF", False), ("currency_strength ON (current default)", True)):
        config.ENABLE_CURRENCY_STRENGTH_FACTOR = flag
        result = await run_backtest(symbols=PAIRS, days=60)
        s = result["stats"]
        print(f"\n=== {label} ===")
        print(f"trades={s['total_trades']} win_rate={s['win_rate']} profit_factor={s['profit_factor']} "
              f"pnl_gross={s['pnl_gross']} commission={s['commission_paid']} pnl_net={s['pnl_net']} "
              f"drawdown={s['drawdown_pct']}")


if __name__ == "__main__":
    asyncio.run(main())
