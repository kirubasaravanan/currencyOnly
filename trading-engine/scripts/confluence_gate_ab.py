"""A/B comparison, now that the cooldown-timestamp bug is fixed: does
requiring confluence_score to ALSO independently clear the threshold
(the original 'stacked gate' design) help or hurt, versus only using it
to scale position size (current default)? Same 60-day/17-pair window both
times. Also dumps full by-symbol breakdown for both variants."""
import asyncio
import json
import sys

sys.path.insert(0, ".")

import config
from config import PAIRS
from engine.backtester import run_backtest


async def main():
    for label, flag in (("confluence sizes only (current)", False), ("confluence also gates (original)", True)):
        config.REQUIRE_CONFLUENCE_GATE = flag
        result = await run_backtest(symbols=PAIRS, days=60)
        s = result["stats"]
        print(f"\n=== {label} ===")
        print(f"trades={s['total_trades']} win_rate={s['win_rate']} profit_factor={s['profit_factor']} "
              f"pnl_gross={s['pnl_gross']} commission={s['commission_paid']} pnl_net={s['pnl_net']} "
              f"drawdown={s['drawdown_pct']}")
        print("by_symbol:")
        for sym, row in sorted(s["by_symbol"].items(), key=lambda kv: kv[1]["pnl_net"], reverse=True):
            print(f"  {sym:8s} trades={row['trades']:3d} win_rate={row['win_rate']:5.1f}% "
                  f"gross={row['pnl_gross']:9.2f} commission={row['commission']:8.2f} net={row['pnl_net']:9.2f}")


if __name__ == "__main__":
    asyncio.run(main())
