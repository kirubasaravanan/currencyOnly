"""Static configuration for the currencyOnly FX-paper-trading engine.

FX-only — no gold anywhere. Per-pair calibration values in PAIR_CALIBRATION
are ported 1:1 from Desktop/V109-Currency-Fixed.pine's per-ticker blocks
(the user's live TradingView strategy) as starting points — not yet
re-validated against real data. That validation is what the backtester
exists for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Tuple


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

PAIRS: List[str] = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD",
    "GBPCAD", "GBPJPY", "AUDJPY", "AUDCAD", "GBPAUD", "EURCAD", "GBPNZD",
    "NZDCAD", "EURGBP", "CHFJPY",
]

MAJORS: List[str] = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD"]

# Named symbol-scope presets — groundwork for eventually routing different
# FundedNext accounts to different symbol subsets (majors-only, all 17,
# top-N by performance, a single pair), per explicit user request. Only
# the two STATIC scopes are defined here; "top_3"/"top_10"/"top_N" are
# deliberately NOT hardcoded from any single backtest sample -- ranking a
# pair as "top" from one 60-day window and freezing that ranking into a
# permanent preset is exactly the overfitting trap already identified and
# explicitly avoided for PAIR_GATE_MODE_OVERRIDE above (e.g. GBPAUD swung
# from best to worst pair between two otherwise-identical backtest runs).
# Use engine.analytics.top_n_symbols(trades, n) to compute a top-N scope
# live, from whatever trade history (live paper trades once accumulated,
# or an explicit backtest result if you really want one) is actually being
# asked about, at the time it's asked -- never frozen into a static list
# here. A "single pair" scope is just picking any one symbol from PAIRS
# directly; no preset needed.
#
# No FundedNext connection code exists anywhere in this repo -- this is
# symbol-scope configuration only, for whenever that becomes a real,
# explicit next step.
SYMBOL_SCOPES: Dict[str, List[str]] = {
    "majors": MAJORS,
    "all_17": PAIRS,
}

OANDA_SYMBOL_MAP: Dict[str, str] = {
    "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD", "USDJPY": "USD_JPY",
    "AUDUSD": "AUD_USD", "NZDUSD": "NZD_USD", "USDCHF": "USD_CHF",
    "USDCAD": "USD_CAD", "GBPCAD": "GBP_CAD", "GBPJPY": "GBP_JPY",
    "AUDJPY": "AUD_JPY", "AUDCAD": "AUD_CAD", "GBPAUD": "GBP_AUD",
    "EURCAD": "EUR_CAD", "GBPNZD": "GBP_NZD", "NZDCAD": "NZD_CAD",
    "EURGBP": "EUR_GBP", "CHFJPY": "CHF_JPY",
}

# Yahoo Finance fallback tickers (=X convention), used only if OANDA fails.
YAHOO_SYMBOL_MAP: Dict[str, str] = {p: f"{p}=X" for p in PAIRS}

OANDA_GRANULARITY_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D",
}
YF_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "60m", "1d": "1d",
}
YF_PERIOD_MAP = {
    "1m": "7d", "5m": "60d", "15m": "60d", "1h": "730d", "4h": "730d", "1d": "5y",
}

# 15m entry — V109's sweepLB/mssLB lookbacks (5-25 bars) read as swing counts
# on a 15m chart, not 5m noise or 1h coarseness.
MTF_TIMEFRAMES: List[str] = ["1d", "4h", "1h", "15m", "5m"]
ENTRY_TIMEFRAME = "15m"
ENTRY_TIMEFRAME_MINUTES = 15
STRUCTURE_TIMEFRAME = "15m"
# Entry TRIGGER (MSS confirmation + actual fill price) is evaluated on this
# finer timeframe; sweep/liquidity-pool levels and SL/TP sizing still use
# STRUCTURE_TIMEFRAME (15m) -- HTF for structure/targets, LTF for entry
# timing, per explicit user instruction.
TRIGGER_TIMEFRAME = "5m"
# Toggle for the HTF-structure/LTF-trigger split above, kept as a flag
# (not a hard rewrite) specifically so the two approaches can be
# A/B-backtested against each other on the same window. False falls back
# to evaluating MSS confirmation + fill price on STRUCTURE_TIMEFRAME (15m)
# only, same as before this feature existed.
#
# [RESULT 2026-08-16] A/B tested on a real 14-day/17-pair window
# (scripts/trigger_timeframe_ab.py): 15m-only produced 16 trades/81.2% win
# rate/PF 7.75/+$812.84; the 5m-trigger version produced 17 trades (barely
# more -- not the volume win hoped for) at 52.9% win rate/PF 0.34/-$246.73.
# 5m price action is dominated by noise relative to V109's mss_lb lookback
# values (calibrated for a coarser timeframe), so MSS on 5m fires on noise,
# not genuine structure shifts. Defaulting back to False; the flag/code
# path stays available for a future attempt with 5m-specific recalibration.
USE_TRIGGER_TIMEFRAME = False

CONTRACT_SIZE_USD: Dict[str, float] = {p: 100_000.0 for p in PAIRS}
PIP_SIZE: Dict[str, float] = {p: (0.01 if "JPY" in p else 0.0001) for p in PAIRS}


# ---------------------------------------------------------------------------
# Paper-broker cost model — raw spread + per-lot commission (ECN/prop-firm
# style, per explicit user choice). Estimated placeholders — swap for
# FundedNext's actual published raw spreads/commission once known; each is
# a single number to edit, and every trade tracks commission separately
# from pnl so gross vs. net is always visible.
# ---------------------------------------------------------------------------

RAW_SPREAD_PIPS: Dict[str, float] = {
    "EURUSD": 0.2, "GBPUSD": 0.3, "USDJPY": 0.2, "AUDUSD": 0.3,
    "NZDUSD": 0.5, "USDCHF": 0.4, "USDCAD": 0.4, "GBPCAD": 1.2,
    "GBPJPY": 0.6, "AUDJPY": 0.5, "AUDCAD": 0.6, "GBPAUD": 1.2,
    "EURCAD": 1.0, "GBPNZD": 1.8, "NZDCAD": 1.2, "EURGBP": 0.5,
    "CHFJPY": 0.8,
}
COMMISSION_PER_LOT_PER_SIDE_USD = 3.50  # ~$7 round-turn per standard 100k lot


# ---------------------------------------------------------------------------
# Per-pair V109 calibration table
# ---------------------------------------------------------------------------

def _ist(spec: str) -> Tuple[Tuple[int, int], ...]:
    """Parse a V109-style session spec e.g. '0830-1030,1130-1900' (IST,
    HHMM, comma-separated windows) into ((start_min, end_min), ...) minutes-
    of-day tuples. Kept as a literal parse of the Pine string (not
    hand-computed minutes) so each entry below can be eyeballed against the
    source script directly."""
    windows = []
    for part in spec.split(","):
        start_s, end_s = part.split("-")
        start_min = int(start_s[:2]) * 60 + int(start_s[2:])
        end_min = int(end_s[:2]) * 60 + int(end_s[2:])
        windows.append((start_min, end_min))
    return tuple(windows)


@dataclass(frozen=True)
class PairCalibration:
    session_windows_ist: Tuple[Tuple[int, int], ...]  # (start_min, end_min) of day, IST
    stop_mult: float = 1.00
    tp_mult: float = 3.00
    tp2_mult: float = 2.20
    para_thresh: float = 0.50
    sweep_lb: int = 20
    sweep_mem: int = 15
    mss_lb: int = 5
    max_sl_pips: int = 40
    min_sl_pips: int = 8
    min_base: float = 0.0005
    conf_boost: float = 0.0
    use_trend_filter: bool = True       # V109 pairUseFilter
    activation_usd: float = 40.0        # profit-trail activation ($)
    activation_pct: float = 50.0        # profit-trail activation (% of TP)
    trail_lock_pct: float = 50.0        # profit-trail locked-profit %


PAIR_CALIBRATION: Dict[str, PairCalibration] = {
    "GBPUSD": PairCalibration(
        session_windows_ist=_ist("1030-1700"), stop_mult=1.80, tp_mult=1.80,
        sweep_lb=25, mss_lb=6, max_sl_pips=45, min_sl_pips=10, min_base=0.0007,
        conf_boost=0.05, use_trend_filter=False, activation_usd=20.0,
    ),
    "GBPCAD": PairCalibration(
        session_windows_ist=_ist("0830-1030,1130-1900"), stop_mult=1.20, tp_mult=1.40,
        sweep_lb=25, mss_lb=6, max_sl_pips=50, min_sl_pips=10, min_base=0.0007,
        conf_boost=0.05, use_trend_filter=False, activation_usd=50.0,
    ),
    "EURUSD": PairCalibration(
        session_windows_ist=_ist("1130-1900"), stop_mult=1.00, tp_mult=3.00,
        use_trend_filter=True, activation_usd=30.0,
    ),
    "USDJPY": PairCalibration(
        session_windows_ist=_ist("0530-0930,1730-1930"), stop_mult=1.00, tp_mult=3.00,
        tp2_mult=2.50, min_base=0.05, max_sl_pips=35, min_sl_pips=6, para_thresh=0.50,
        use_trend_filter=False, activation_usd=40.0,
    ),
    "AUDUSD": PairCalibration(
        session_windows_ist=_ist("0530-0930,1130-1430,1630-1900"), stop_mult=1.00,
        tp_mult=2.25, max_sl_pips=30, min_sl_pips=5, min_base=0.0004, para_thresh=0.40,
        use_trend_filter=True, activation_usd=25.0,
    ),
    "NZDUSD": PairCalibration(
        session_windows_ist=_ist("0530-0930,1130-1430,1630-1900"), max_sl_pips=35,
        min_sl_pips=6, min_base=0.0004, para_thresh=0.45, use_trend_filter=False,
        activation_usd=25.0,
    ),
    "USDCHF": PairCalibration(
        session_windows_ist=_ist("0830-1130,1430-1830"), stop_mult=1.30, tp_mult=1.50,
        max_sl_pips=40, min_sl_pips=8, min_base=0.0005, para_thresh=0.50,
        use_trend_filter=False, activation_usd=30.0,
    ),
    "USDCAD": PairCalibration(
        session_windows_ist=_ist("0530-0930,1130-1930"), stop_mult=1.00, tp_mult=2.00,
        max_sl_pips=40, min_sl_pips=8, min_base=0.0005, para_thresh=0.50,
        use_trend_filter=True, activation_usd=40.0,
    ),
    "GBPJPY": PairCalibration(
        session_windows_ist=_ist("1230-1700"), stop_mult=1.20, tp_mult=2.40,
        min_base=0.05, para_thresh=0.55, use_trend_filter=False,
        activation_usd=100.0, activation_pct=90.0, trail_lock_pct=0.0,
    ),
    "AUDJPY": PairCalibration(
        session_windows_ist=_ist("0530-1000,1730-2000"), stop_mult=1.20, tp_mult=2.80,
        min_base=0.05, max_sl_pips=45, min_sl_pips=8, para_thresh=0.45,
        use_trend_filter=False, activation_usd=40.0,
    ),
    "AUDCAD": PairCalibration(
        session_windows_ist=_ist("1100-1430,1630-1700"), stop_mult=0.90, tp_mult=2.40,
        max_sl_pips=40, min_sl_pips=6, min_base=0.0004, para_thresh=0.45,
        use_trend_filter=False, activation_usd=25.0,
    ),
    "GBPAUD": PairCalibration(
        session_windows_ist=_ist("1130-1430,1630-1900"), stop_mult=2.20, tp_mult=2.80,
        max_sl_pips=40, min_sl_pips=6, min_base=0.0004, para_thresh=0.45,
        use_trend_filter=False, activation_usd=25.0,
    ),
    "EURCAD": PairCalibration(
        session_windows_ist=_ist("0800-1900"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=40.0,
    ),
    "GBPNZD": PairCalibration(
        session_windows_ist=_ist("0800-1900"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=40.0,
    ),
    "NZDCAD": PairCalibration(
        session_windows_ist=_ist("0800-1900"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=40.0,
    ),
    "EURGBP": PairCalibration(
        session_windows_ist=_ist("0800-1900"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=40.0,
    ),
    "CHFJPY": PairCalibration(
        session_windows_ist=_ist("0800-1500"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.05, use_trend_filter=True, activation_usd=40.0,
    ),
}

assert set(PAIR_CALIBRATION) == set(PAIRS), "every pair needs a calibration entry"


# ---------------------------------------------------------------------------
# Risk parameters (paper account)
# ---------------------------------------------------------------------------

INITIAL_BALANCE = 10_000.0
RISK_PER_TRADE_PCT = 1.0     # % of equity risked per trade (ATR-sized, unlike
                              # V109's flat $110 — scales with account growth)
DAILY_LOSS_LIMIT = 3.0       # % of equity, account-wide across all 17 pairs
WEEKLY_LOSS_LIMIT = 6.0
MAX_OPEN_TRADES = 5
MAX_DRAWDOWN_PCT = 10.0
LOT_BOUNDS: Dict[str, Tuple[float, float]] = {p: (0.01, 0.50) for p in PAIRS}
COOLDOWN_BARS = 12  # bars since last exit on ENTRY_TIMEFRAME (15m -> 3h)

BE_OFFSET_PCT = 0.25          # V109 beOffsetPct — global, not per-pair
EARLY_BE_TRIGGER_PCT = 0.90   # V109: move to BE once 90% of the way to TP1

# Exit-management mode itself lives on the mutable `state` object below
# (config.state.exit_mode), not as a static constant here -- see
# EngineState's own field comment for why.


# ---------------------------------------------------------------------------
# Sessions (UTC) + per-session dominant currencies
# ---------------------------------------------------------------------------

SESSIONS_UTC = {
    "asian":      (0, 7),
    "london":     (7, 12),
    "overlap":    (12, 16),   # London + NY overlap — highest liquidity
    "newyork":    (16, 21),
    "offsession": (21, 24),
}

# From the forex-sessions research notes: which currencies actually trade
# actively in each session. Used by engine/session_dominance.py to reward
# pairs with exactly one dominant currency and penalize pairs where both
# legs are simultaneously active (murkier, "battling" price action).
SESSION_DOMINANT_CURRENCIES: Dict[str, FrozenSet[str]] = {
    "asian":      frozenset({"JPY", "AUD", "NZD"}),
    "london":     frozenset({"GBP", "EUR", "CHF"}),
    "overlap":    frozenset({"USD", "GBP", "EUR", "CHF", "CAD"}),
    "newyork":    frozenset({"USD", "CAD", "GBP", "EUR", "CHF"}),
    "offsession": frozenset(),
}


# ---------------------------------------------------------------------------
# Economic calendar (high-impact) — reused as-is, symbol-agnostic already
# ---------------------------------------------------------------------------

HIGH_IMPACT_EVENTS = {
    "CPI", "PPI", "NFP", "Nonfarm Payroll",
    "FOMC", "Interest Rate Decision", "Fed Chair",
    "GDP", "PMI", "Retail Sales", "Unemployment",
}
EVENT_IMPACT_TIERS = [
    ("TIER_1_CRITICAL", {"NFP", "Nonfarm Payroll", "FOMC", "Interest Rate Decision", "Fed Chair", "CPI"}, 60),
    ("TIER_2_HIGH", {"GDP", "PPI", "Retail Sales"}, 30),
    ("TIER_3_MODERATE", {"PMI", "Unemployment", "Employment Change", "Claims"}, 20),
]
NEWS_BLACKOUT_MINUTES = 30


# ---------------------------------------------------------------------------
# Correlation — net per-currency exposure cap (generalized beyond a fixed
# USD-only group list, since this universe is heavy on non-USD crosses)
# ---------------------------------------------------------------------------

MAX_TRADES_PER_CURRENCY_EXPOSURE = 2


# ---------------------------------------------------------------------------
# Confluence entry engine
# ---------------------------------------------------------------------------

BASE_CONFIDENCE_THRESHOLD = 0.65  # V109 baseConf; + per-pair conf_boost
# A/B toggle for isolating currency_strength's real effect on trade count
# vs. quality (see scripts/currency_strength_ab.py) -- when False, the
# factor is excluded from BOTH the weighted sum and the normalizing
# denominator (not just neutralized to 0.5), so the confidence bar is
# measured on the same basis with vs. without it.
ENABLE_CURRENCY_STRENGTH_FACTOR = True

# A/B toggle (see scripts/confluence_gate_ab.py and entry.py's use of it).
# [RESULT 2026-08-16] Tested clean (cooldown-timestamp bug AND position-
# sizing bug both fixed first) on a real 60-day/17-pair window: gating
# (True) produced 583 trades/64.8% win rate/PF 1.47/+$4498.32/1.13% max DD
# vs. sizing-only (False)'s 915 trades/64.7%/PF 1.27/+$3842.87/3.56% DD --
# fewer trades but meaningfully better profit factor, net P&L, and a third
# of the drawdown. Matches the pattern all session: quality-filtering has
# consistently helped, loosening for volume has consistently hurt.
REQUIRE_CONFLUENCE_GATE = True

# Per-pair override for REQUIRE_CONFLUENCE_GATE — {"SYMBOL": True/False}.
# Deliberately empty. The per-pair breakdown of the same 60-day run above
# showed some pairs individually "preferring" the other setting (e.g.
# GBPAUD: +$983 net under gating vs. -$151 under sizing-only, a ~$1,134
# swing on just 34-46 trades) — but picking a per-pair winner FROM that
# same single backtest sample would be fitting noise, not a real edge: a
# swing that large on that few trades usually means a handful of outlier
# trades are dominating, not a persistent property of the pair. Explicit
# user decision 2026-08-16: run 2 weeks of live paper observation with
# ZERO logic changes first, and only populate this dict once a pair has
# enough of its OWN live/forward trade history to justify a genuine
# per-pair decision — not from a single historical backtest window.
PAIR_GATE_MODE_OVERRIDE: Dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Engine cadence
# ---------------------------------------------------------------------------

ENGINE_LOOP_SECONDS = 60
ANALYSIS_TTL_SECONDS = 60


@dataclass
class EngineState:
    running: bool = False
    started_at: float = 0.0
    last_scan_at: float = 0.0
    scan_count: int = 0
    # Runtime-adjustable via POST /threshold (dashboard's ThresholdSlider);
    # entry.py reads this instead of the BASE_CONFIDENCE_THRESHOLD constant
    # directly so a live change takes effect without a restart.
    confidence_threshold: float = BASE_CONFIDENCE_THRESHOLD
    # Runtime-adjustable via POST /exit-mode, same reasoning: needs to be
    # flippable without a restart so "dynamic" vs "static" exit management
    # can actually be A/B'd on real live outcomes over a few days each,
    # per explicit user instruction 2026-08-16. paper_broker.py and
    # tradesgnl_relay.py both read config.state.exit_mode directly (NOT a
    # `from config import EXIT_MODE`-style static import, which would bind
    # the value at import time and never see a later change) so the two
    # can never silently diverge. "dynamic" or "static" -- see either
    # module's own comments for what each mode actually does.
    exit_mode: str = "dynamic"


state = EngineState()
