# currencyOnly — FX-Only Hybrid-Gate Paper Trading Engine

A standalone, FX-pairs-only trading engine — 17 majors/crosses, paper trading only.

> ⚠️ **This app never places, modifies, or closes a real order anywhere — not even on an OANDA demo/practice account.** OANDA is used strictly as a read-only market-data source (`/v3/instruments/{instrument}/candles`). Every position is simulated entirely in-process. There is deliberately no `OANDA_ACCOUNT_ID` anywhere in this repo — order-placement endpoints require one, so its absence is a structural guarantee, not just a policy. Verify yourself: `grep -rn "requests.post\|httpx.post\|/v3/accounts\|/orders" trading-engine/` should only ever match comments.

## Why this exists

The sibling app at `../Forex/Forex` trades XAUUSD (live, real money across 6 relayed accounts) and 7-8 FX pairs through one shared confluence engine — but the FX side underperforms gold there, and its paper broker never modeled commission. This app is a from-scratch, FX-only redesign combining:

- **V109** (`Desktop/V109-Currency-Fixed.pine`, the user's live TradingView strategy) — liquidity-sweep + market-structure-shift entry timing, per-pair session calibration, ADR-exhaustion TP/SL scaling, profit-based trailing stop.
- **The existing engine's proven infrastructure** — indicator library, multi-timeframe alignment, correlation filtering, news-blackout calendar.
- **A hybrid gate**: V109 owns entry *timing* (Layer 1 hard gate), a weighted confluence score owns entry *quality* (Layer 2), both must pass.
- **Two new factors** not in either source: session-currency-dominance (from forex-session research — reward pairs with one dominant currency per session, penalize "battling" pairs like AUDJPY in the Asian session) and measured currency-strength ranking (trade a strong currency against a weak one for directional clarity).
- **Structural SL/TP placement**: stops sit just beyond the nearest opposing liquidity level (not short of it, where noise could trigger without a real break), targets sit just before the nearest same-side level (price reacts at levels more often than it punches through).
- **Commission-aware paper broker**: raw spread + $ commission per lot, charged on both entry and exit, tracked separately from P&L so gross/commission/net is always visible.

## Architecture

```
trading-engine/                Python 3.12, FastAPI (port 8001)
├── config.py                  17-pair symbol maps, V109 per-pair calibration table, cost model
├── data/market_data.py        OANDA (primary, data-only) -> Yahoo Finance (fallback)
├── indicators/indicators.py   EMA/RSI/ADX/ATR/Choppiness/VWAP/Supertrend/OB/FVG/MSS/ADR
└── engine/
    ├── entry.py                Hybrid-gate signal engine (Layer 1 + Layer 2)
    ├── liquidity_pools.py       Equal highs/lows, PDH/PDL/PWH/PWL, round numbers
    ├── session_dominance.py     Per-session dominant-currency scoring
    ├── currency_strength.py     Measured relative-momentum ranking
    ├── mtf_trend.py             Multi-timeframe bias alignment
    ├── macro_filter.py          Forex Factory calendar + tiered news blackout, DXY/US10Y USD bias
    ├── risk.py                  ATR position sizing, daily/weekly loss + drawdown gates
    ├── correlation.py           Net per-currency exposure cap (not fixed USD-only groups)
    ├── paper_broker.py          Commission-aware local simulator — zero order-placement code
    ├── trade_manager.py         Per-pair session close (shared by live + backtest), cooldown
    ├── backtester.py            Walk-forward replay over real OANDA history
    ├── analytics.py             Win rate/expectancy/profit factor/drawdown, gross-vs-net split
    └── orchestrator.py          60s live scan loop

src/                            Next.js 16 dashboard (port 3006)
mini-services/trading-engine/   Node.js supervisor (auto-restarts uvicorn on crash)
```

## Setup

```bash
cd trading-engine
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env   # fill in OANDA_API_TOKEN (data-only scope — no account ID needed)
```

```bash
bun install   # from repo root
```

## Run

```bash
bash .zscripts/dev.sh
```

Or separately:

```bash
# Engine
cd trading-engine && .venv/Scripts/python -m uvicorn main:app --port 8001

# Dashboard
bun run dev
```

Open http://localhost:3006.

## Status (as of this build)

First real backtest (17 pairs, 60 days of live OANDA history): 17 trades, 70.6% win rate, profit factor 1.74, **net +$156.60 after $43.75 commission**. Genuinely promising, but trade frequency is low (~1 trade/pair/2.4 months) — diagnosed to `scripts/gate_diagnostics.py`: Layer 2's weighted-confluence threshold is the dominant bottleneck, not Layer 1's structural gate. The confidence threshold is runtime-adjustable (`POST /threshold`, no restart needed) specifically so this can be tuned against real backtest data rather than guessed. See `scripts/threshold_sweep.py` for a frequency-vs-quality comparison at a few threshold levels.

Every per-pair calibration value in `config.py`'s `PAIR_CALIBRATION` table is a **starting point ported from V109**, not yet independently re-validated — that validation is what the backtester and this observation period are for.

## Path to real money

Per explicit instruction: **paper only, for now.** No OANDA demo orders, no MT5, no webhook relays of any kind. Once enough paper/backtest data supports it, the next step is a FundedNext challenge account — not before.

## License

Educational/personal use. Not financial advice.
