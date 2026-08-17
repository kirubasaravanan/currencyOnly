# currencyOnly — FX-Only Hybrid-Gate Paper Trading Engine

A standalone, FX-pairs-only trading engine — 17 majors/crosses, paper trading by default.

> ⚠️ **OANDA is always read-only.** It's used strictly as a market-data source (`/v3/instruments/{instrument}/candles`) — there is deliberately no `OANDA_ACCOUNT_ID` anywhere in this repo, so OANDA specifically can never place, modify, or close a real order from this app. Every position is otherwise simulated entirely in-process by the paper broker.
>
> **[UPDATED 2026-08-16] An optional real-order relay now exists.** `engine/tradesgnl_relay.py`, wired into the live orchestrator only (never the backtester), mirrors every paper entry/exit to a real MT5 account via TradeSgnl — *only when `TRADESGNL_LICENSE_ID` is set in `.env`*. Currently configured against a demo account (login 110875560, MetaQuotes-Demo server, ~$15k), explicitly verified isolated from the sister Forex app's own use of that same license before being wired up — see that module's docstring for the full verification trail. Leave `TRADESGNL_LICENSE_ID` blank to disable this relay entirely and return to pure paper-only, no-order-placement-code behavior. Verify current state yourself: `grep -rn "requests.post\|httpx.post\|/v3/accounts\|/orders" trading-engine/` — any match outside `tradesgnl_relay.py` and its docstrings means something unexpected changed.

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
    ├── discord_alerts.py        Entry/exit/session/EOD alerts — live-only, notification-only
    ├── tradesgnl_relay.py       OPTIONAL real MT5 relay — live-only, off unless TRADESGNL_LICENSE_ID is set
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

After fixing two real bugs found via direct data investigation this session — a cooldown-timestamp bug that was capping every pair at exactly 1 trade regardless of gate/confidence logic, and a position-sizing bug that silently mis-sized every non-USD-quoted pair (USDJPY undersized ~150x) — a 60-day/17-pair backtest produces ~500+ trades with a healthy profit factor and a 45-minute pre-close entry cutoff (empirically confirmed to remove structurally-doomed late entries). Every per-pair calibration value in `config.py`'s `PAIR_CALIBRATION` table is still a **starting point ported from V109**, not yet independently re-validated — that validation is what the live observation period is for.

## Path to real money

Originally: **paper only.** No OANDA demo orders, no MT5, no webhook relays of any kind. That changed 2026-08-16 by explicit instruction: `engine/tradesgnl_relay.py` now optionally mirrors every paper entry/exit to a real MT5 demo account via TradeSgnl, gated entirely on `TRADESGNL_LICENSE_ID` being set in `.env` — see that module's docstring and the top-of-file warning above for the full verification trail (this license was confirmed isolated from the sister Forex app's own use of it before being wired up). Leave the license unset to stay pure paper-only. The next step after this demo relay proves out is a FundedNext challenge account.

## License

Educational/personal use. Not financial advice.
