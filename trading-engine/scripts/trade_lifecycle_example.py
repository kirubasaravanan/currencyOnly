"""Read-only: dump FULL trade records (every field, not just the summary
columns shown in the dashboard/earlier reports) for EURUSD, to explain
exactly how partial-TP + break-even + profit-trailing produce their
final exit reason and P&L."""
import asyncio
import json
import sys

sys.path.insert(0, ".")

from engine.backtester import run_backtest


async def main():
    result = await run_backtest(symbols=["EURUSD"], days=60)
    trades = [t for t in result["closed_trades"] if t["symbol"] == "EURUSD"]
    print(f"{len(trades)} EURUSD trades\n")
    for t in trades:
        print(json.dumps(t, indent=2, default=str))
        print("---")


if __name__ == "__main__":
    asyncio.run(main())
