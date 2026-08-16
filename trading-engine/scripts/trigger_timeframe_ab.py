"""A/B comparison: MSS-confirmation + entry price on 5m TRIGGER timeframe
(SL/TP still sized on 15m) vs. the 15m-only fallback -- same 14-day window
for both, since that's the longest span OANDA's 5000-candle cap lets 5m
data cover in one fetch."""
import asyncio
import sys

sys.path.insert(0, ".")

import config
from config import PAIRS
from engine.backtester import run_backtest


async def main():
    for label, flag in (("15m-only (fallback)", False), ("5m-trigger (new)", True)):
        config.USE_TRIGGER_TIMEFRAME = flag
        result = await run_backtest(symbols=PAIRS, days=14)
        s = result["stats"]
        print(f"\n=== {label} ===")
        print(f"trades={s['total_trades']} win_rate={s['win_rate']} profit_factor={s['profit_factor']} "
              f"pnl_gross={s['pnl_gross']} commission={s['commission_paid']} pnl_net={s['pnl_net']} "
              f"drawdown={s['drawdown_pct']}")


if __name__ == "__main__":
    asyncio.run(main())
