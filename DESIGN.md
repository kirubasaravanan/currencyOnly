# currencyOnly — Design Document

**Status:** Paper trading only, real OANDA data, zero order-placement code anywhere.

## 1. Why a separate app, not a change to Forex/Forex

`Forex/Forex` trades XAUUSD live with real money across 6 relayed accounts, sharing one confluence engine with its FX pairs. Two problems with extending that app instead of building fresh:

1. **Blast radius.** Any bug in a shared engine risks the live gold accounts. A standalone repo makes that structurally impossible — this app doesn't import from, deploy alongside, or share any credential scope with the live one beyond reading the same OANDA data token.
2. **FX needed different logic, not the same logic retuned.** The user's V109 TradingView strategy (liquidity sweep, market-structure-shift, per-pair session windows, ADR-exhaustion scaling, profit-based trailing) was never fully ported into the live engine's 10-factor confluence scorer — they're genuinely different entry philosophies. Bolting V109 onto the live engine's `entry.py` would have meant either replacing gold's working logic or forking the file — a separate app is cleaner than either.

## 2. The hybrid gate

Two independent, both-must-pass layers:

- **Layer 1 (V109-derived hard gate):** per-pair IST session window, no news blackout, a liquidity-pool sweep within the pair's memory window, market-structure-shift confirming the same direction, 1H EMA trend + slope alignment, not parabolically extended against the trade direction, and (for pairs with V109's `pairUseFilter` on) the structural break sustained over the last few bars, not a single-bar spike.
- **Layer 2 (weighted confluence score):** MTF alignment, ADX trending, RSI direction, VWAP confirmation, supertrend, OB/FVG proximity, session-currency-dominance, and measured currency-strength — summed by weight, divided by total positive weight, penalized for choppiness, must clear the pair's threshold.

V109 owns *timing* (it's a proven, pair-specific entry mechanism from live trading). The confluence score owns *quality* (multi-signal confirmation before committing capital). Neither alone was judged sufficient — this was an explicit user decision among three options (V109 alone, confluence-only retuned, or hybrid), see the approved plan.

**Two new factors not in either source**, both added mid-build in response to specific ideas:

- *Session-currency dominance* — from forex-session research notes: each session (Asian/London/NY/overlap) has a set of currencies actually active in it. A pair with exactly one dominant currency gets a clean directional push; a pair with both currencies dominant (AUDJPY in the Asian session) tends to chop as the two active currencies fight. Scored, not gated — V109's own session windows already control *whether* a pair trades; this refines confidence *within* that window.
- *Currency strength ranking* — measured momentum (% return over the last 50 hourly bars) per currency across every pair it appears in. Rewards trades that pair a currency actually strengthening against one actually weakening; penalizes pairing two currencies with similar momentum, which tends to range rather than trend. Complementary to session dominance: one is about *when* a currency is typically active, this is about *what it's actually doing right now*.

## 3. Structural SL/TP placement

Added after the initial build, in response to a direct observation: a stop or target placed with no awareness of nearby structural levels is either too tight (ordinary noise triggers it before a real trend change) or badly targeted (price reacts at levels more often than it powers through them). `entry.py`'s `_structural_sl_tp()` nudges the ATR-based stop just beyond the nearest opposing liquidity-pool level when the raw ATR stop would sit short of it — and skips the trade entirely if respecting that level would need a wider stop than the pair's configured max, rather than taking a structurally unsafe trade. The take-profit (TP1) is pulled back to just before the nearest same-side level when one exists closer than the raw ATR target, since a level breaking either way is what actually signals continuation vs. reversal.

## 4. Commission-aware paper broker, by design not accident

The live app's paper broker models spread but never commission — confirmed directly from its own code comments. This app's broker (explicit user choice: raw spread + $ commission per lot per side) charges commission on both entry and exit, tracks it as its own `commission_paid` field separate from `pnl`, so every trade, every backtest run, and the dashboard's KPI row all show gross P&L, commission drag, and net P&L as three distinct numbers. `RAW_SPREAD_PIPS` and `COMMISSION_PER_LOT_PER_SIDE_USD` in `config.py` are explicitly flagged as estimated placeholders — swap for FundedNext's real published rates once known, one constant each.

The 50%-partial-at-TP1-then-runner-to-TP2 mechanic (V109's own "SINGLE-TRADE SCALE-OUT EDITION" design) and the profit-based trailing stop are both fully implemented here, unlike the live app — which disabled its equivalent (`PAPER_SL_DYNAMICS_ENABLED = False`) because its real broker relay can't modify SL/TP mid-trade. That constraint doesn't exist here; there is no relay at all.

## 5. What's still a starting point, not a validated conclusion

Every value in `config.py`'s `PAIR_CALIBRATION` table (session windows, stop/TP multiples, SL pip bounds, ADR thresholds, trailing-stop activation) is ported directly from V109's own per-ticker blocks. They were the user's live, real-money-informed settings for those pairs — a reasonable starting point — but this Python re-implementation, the added confluence layer, and the structural SL/TP logic have never been checked against real data until this build's own verification pass.

**First real verification** (17 pairs, 60 days of live OANDA history): 17 trades, 70.6% win rate, profit factor 1.74, net +$156.60 after $43.75 commission. Promising, but trade frequency (~1 trade/pair/2.4 months) prompted a diagnostic pass (`scripts/gate_diagnostics.py`) that found Layer 2's confluence threshold — not Layer 1's structural gate — is the dominant bottleneck (202 bars fully passed Layer 1 for GBPUSD alone in that window; only 1 became an actual trade). The confidence threshold is runtime-adjustable (`POST /threshold`) specifically so this is a live tuning decision against real data, not a code change. `scripts/threshold_sweep.py` compares trade frequency and quality across a few threshold levels.

A genuine bug was also found and fixed during this same pass: the initial approximation of V109's `pairUseFilter` (2-bar delayed confirmation) required the market-structure-shift trigger to independently re-fire at two specific bars — stacking two rare events — instead of checking the break was sustained over a short window. That cut candidate trades by roughly 75% on the 8 affected pairs versus what V109's own intent was.

## 6. Explicitly out of scope for this build

- **Gold/XAUUSD Fibonacci trend-following** (from the same research video that inspired session-dominance) — general-purpose TA, not gold-specific, but a different entry paradigm from the V109 hybrid gate built here. Belongs in `Forex/Forex` (gold's home) or as a separate future strategy variant, not folded into this FX-only engine.
- **Commodity/fundamental correlation** (oil↔CAD, gold↔AUD, etc.) — the live app's `currency_profile.py` covers this but needs point-in-time fundamental data that risks backtest lookahead bias if added carelessly. Addable later as a lightweight, backtest-safe direction read (same pattern as the existing DXY/US10Y macro read), not built here.
- **Optimizer, TP-mode comparison, Monte Carlo risk metrics, multi-account status** — all present in the live app's dashboard, all deferred here until the core hybrid-gate engine has enough real paper/backtest data to be worth optimizing further.
