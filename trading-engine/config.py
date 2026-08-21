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

# [FIX 2026-08-21, explicit user instruction] EURGBP removed entirely --
# three independent signals all pointed the same way: live paper was net
# -$53.37 over its only 3 trades (small sample, but consistently
# negative), the 60-day backtest showed only +$20.14/34 trades (barely
# above noise, not a real edge), and the theoretical session review found
# EUR and GBP share almost identical dominant sessions, so this pair
# structurally never gets a single clean directional session -- only
# "dead" or "both currencies fighting each other." Matches the user's own
# trading-experience read that this pair chops.
# [ADD 2026-08-21, explicit user instruction] Trial addition -- EURJPY,
# NZDJPY, CADJPY, EURAUD, EURNZD. Flagged as good candidates (liquid
# crosses pairing currencies from DIFFERENT correlation groups --
# European/risk-on vs JPY's safe-haven/funding role for the first three,
# European vs commodity/Asian-Pacific for the last two -- the same
# "opposite reaction to sentiment" dynamic that makes AUDJPY trade well,
# not the same-group correlation that made EURGBP chop). No V109 ported
# values exist for these -- full 24h window for now, pending the same
# hour-by-hour backtest revalidation already done for the original 17.
# User's own instruction: add all 5 for a one-week monitoring period
# regardless of backtest read -- paper AND TradeSgnl relay normally (see
# PINECONNECTOR_EXCLUDED_PAIRS below), since TradeSgnl is demo/no-real-
# money, the same unconstrained-data-source role it already plays for
# every other pair. PineConnector (the real FundedNext relay) stays
# excluded until TradeSgnl's live results over that period are
# satisfactory.
PAIRS: List[str] = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD",
    "GBPCAD", "GBPJPY", "AUDJPY", "AUDCAD", "GBPAUD", "EURCAD", "GBPNZD",
    "NZDCAD", "CHFJPY", "EURJPY", "NZDJPY", "CADJPY", "EURAUD", "EURNZD",
]

# [ADD 2026-08-21, explicit user instruction; scope clarified same day]
# Trial pairs that open/manage trades normally on paper AND relay
# normally to TradeSgnl (demo account, no real money -- exactly the
# "unconstrained data source" role TradeSgnl already plays for every
# other pair) but are NEVER sent to PineConnector (the real
# FundedNext-connected relay) -- orchestrator.py's entry-gating block
# checks this before calling pineconnector_relay.send_entry() only,
# TradeSgnl is untouched by this set entirely. Once TradeSgnl's live
# results over the monitoring period are satisfactory, a pair can be
# promoted off this set to relay normally everywhere. Empty by default
# (every original pair already relays to both).
PINECONNECTOR_EXCLUDED_PAIRS: FrozenSet[str] = frozenset({"EURJPY", "NZDJPY", "CADJPY", "EURAUD", "EURNZD"})

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
    "CHFJPY": "CHF_JPY", "EURJPY": "EUR_JPY", "NZDJPY": "NZD_JPY",
    "CADJPY": "CAD_JPY", "EURAUD": "EUR_AUD", "EURNZD": "EUR_NZD",
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
    "EURCAD": 1.0, "GBPNZD": 1.8, "NZDCAD": 1.2,
    "CHFJPY": 0.8, "EURJPY": 0.6, "NZDJPY": 0.7, "CADJPY": 0.8,
    "EURAUD": 1.0, "EURNZD": 1.4,
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
    # [FIX 2026-08-21, explicit user instruction, 60-day backtest evidence]
    # Original ported window was "0830-1130,1430-1830" -- revalidated after
    # a live 08:32 IST entry underperformed and matched the user's own
    # "Swiss 6am, US midnight" observation. Hour-by-hour backtest showed
    # the morning window (08:30-11:30) net -$190/60d across every hour in
    # it, while 14:00-19:00 was consistently strong (+$454/60d, 75-100%
    # win every hour) -- and isn't a coincidence: 14:00-19:00 IST is
    # 08:30-13:30 UTC, i.e. London's main session through the London-NY
    # handoff, the highest-liquidity window in FX. Morning window dropped
    # entirely; afternoon window widened slightly (1430-1830 -> 1400-1900)
    # to capture two more hours the data showed were also strong.
    "USDCHF": PairCalibration(
        session_windows_ist=_ist("1400-1900"), stop_mult=1.30, tp_mult=1.50,
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
    # [FIX 2026-08-21, explicit user instruction, 60-day backtest evidence]
    # AUDJPY was the worst-performing pair in the live paper track record
    # (-$77.76, PF 0.27) -- original ported windows "0530-1000,1730-2000"
    # netted -$83.68/60d when hour-gated, and the messy, non-contiguous
    # hour-by-hour pattern doesn't support cherry-picking scattered hours
    # (that's overfitting, not a real edge). What DOES hold up: AUD and
    # JPY are both Asian-session currencies, and 05:00-17:00 IST (Asian
    # session through the Tokyo->London handover) nets +$573.92/60d,
    # mostly 80-100% win hours -- while the OLD evening window
    # (17:30-20:00 IST) is pure London hours where neither currency is
    # dominant (per session_dominance.py's own mapping) and was
    # consistently the worst part of the pair's day (-163, -184, -167
    # across 17:00/19:00/20:00). Single contiguous window replacing both.
    "AUDJPY": PairCalibration(
        session_windows_ist=_ist("0500-1700"), stop_mult=1.20, tp_mult=2.80,
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
    # [FIX 2026-08-21, explicit user instruction, 60-day backtest evidence]
    # Original ported window "0800-1900" netted -$149.45/60d, while the
    # hours it excluded netted +$211.49 -- backwards. NZD (Asian-session)
    # and CAD (Overlap/NY-session) don't share a home session the way
    # AUDJPY's two currencies did, so this isn't a single clean block --
    # it's the two convincingly-negative hours (11:00: 25% win, PF 0.05,
    # -$106; 18:00: 40% win, PF 0.19, -$140) carved OUT of an otherwise
    # solid range, plus the convincingly-positive 05:00 hour (n=10, 80%
    # win, PF 2.33, +$130 -- NZD's own Asian home session) added back in.
    "NZDCAD": PairCalibration(
        session_windows_ist=_ist("0500-1100,1200-1800"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=40.0,
    ),
    "CHFJPY": PairCalibration(
        session_windows_ist=_ist("0800-1500"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.05, use_trend_filter=True, activation_usd=40.0,
    ),
    # [ADD 2026-08-21, explicit user instruction] Trial pairs -- see the
    # PAIRS/PAPER_ONLY_PAIRS comment above. No V109 ported values exist,
    # so these are generic starting points (matching PairCalibration's own
    # class defaults, plus JPY-appropriate min_base/max_sl_pips matching
    # the other JPY crosses above) with the session gate left fully open
    # (full 24h) specifically so the backtester can discover where each
    # pair's real edge sits, same hour-by-hour method already used to fix
    # USDCHF/AUDJPY/NZDCAD. Narrow to a real window only after that
    # backtest, and only once genuinely positive -- these stay
    # paper-only (PAPER_ONLY_PAIRS) throughout the observation period.
    "EURJPY": PairCalibration(
        session_windows_ist=_ist("0000-2400"), max_sl_pips=40, min_sl_pips=8,
        min_base=0.05, use_trend_filter=True, activation_usd=30.0,
    ),
    "NZDJPY": PairCalibration(
        session_windows_ist=_ist("0000-2400"), max_sl_pips=45, min_sl_pips=8,
        min_base=0.05, use_trend_filter=False, activation_usd=40.0,
    ),
    "CADJPY": PairCalibration(
        session_windows_ist=_ist("0000-2400"), max_sl_pips=45, min_sl_pips=8,
        min_base=0.05, use_trend_filter=False, activation_usd=40.0,
    ),
    # [FIX 2026-08-21, explicit user instruction, discovery-backtest
    # evidence] EURAUD's full-24h discovery backtest was -$646.77/60d
    # (n=44), but almost every hour is theoretically "clean" for this
    # pair (EUR and AUD are in different correlation groups), so this
    # isn't a session-mismatch story -- it's genuine hour-specific
    # performance. Rather than exclude bad hours (there are too many,
    # and removing even the two worst ones -- 08:00 n=7/-$345, 20:00
    # n=2/-$193 -- still leaves the rest net negative), kept ONLY the
    # three well-sampled (n>=3) genuinely positive hours: 05:00 (n=3,
    # 66.7% win, +$81), 06:00 (n=3, 100% win, +$85), 13:00 (n=4, 75%
    # win, +$149). Deliberately conservative -- thinner (n=1-2) positive
    # hours elsewhere weren't trusted enough to include yet.
    "EURAUD": PairCalibration(
        session_windows_ist=_ist("0500-0700,1300-1400"), max_sl_pips=45, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=30.0,
    ),
    # [FIX 2026-08-21, explicit user instruction, discovery-backtest
    # evidence] EURNZD's full-24h backtest was -$10.83/60d (n=52,
    # essentially breakeven) -- unlike EURAUD, removing just the three
    # well-sampled (n=5 each) genuinely bad hours flips it decisively
    # positive: 00:00 (20% win, -$329), 07:00 (20% win, -$370), 15:00
    # (40% win, -$263) together account for -$962 of drag. Everything
    # else kept (opposite strategy from EURAUD -- here exclusion alone
    # is enough, no need to cherry-pick thin positive hours).
    "EURNZD": PairCalibration(
        session_windows_ist=_ist("0100-0700,0800-1500,1600-2400"), max_sl_pips=45, min_sl_pips=8,
        min_base=0.0005, use_trend_filter=True, activation_usd=30.0,
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

# [ADD 2026-08-18, explicit user instruction] Daily give-back circuit
# breaker -- account-wide, based on REALIZED P&L only (matches
# scripts/daily_giveback_report.py's own simplification: a "protect
# what's already realized" breaker only cares about realized swings).
# Once today's cumulative realized P&L has peaked at DAILY_GIVEBACK_MIN_PEAK
# or more, and then falls back by DAILY_GIVEBACK_PCT% or more from that
# peak, every open position is force-closed (both paper and, via the
# relay, the real MT5 account) and no new entries are taken for the rest
# of the IST calendar day.
#
# Calibrated on a real 60-day/52-trading-day backtest (2026-06-08 to
# 2026-08-18, current REQUIRE_CONFLUENCE_GATE=True code): this exact
# 25%/$100 rule would have triggered on 15 of 52 days (28.8% -- a
# minority, not most days) and taken total P&L from $4,723.77 to an
# estimated $6,536.28 (+$1,812.51, +38%, avg $90.84/day -> $125.70/day).
# The single day that mattered most in this conversation (2026-08-18
# itself) is exactly the profile this is built for: peaked at $117.97,
# ended at -$25.12 with nothing to show for the morning's gain.
DAILY_GIVEBACK_MIN_PEAK = 100.0   # $ -- rule doesn't apply below this peak (avoids acting on noise)
DAILY_GIVEBACK_PCT = 25.0         # % given back from peak that triggers the stop

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
    # [ADD 2026-08-19, explicit user instruction] Manual master kill-switch
    # for NEW real-money entries only -- flippable without a restart via
    # POST /real-relay, same runtime-adjustable pattern as exit_mode above.
    # Gates every real relay's send_entry() (tradesgnl_relay,
    # pineconnector_relay, and any future native-MT5 relay) in one place --
    # orchestrator.py checks this once per entry rather than each relay
    # module needing its own copy of the same flag. Deliberately does NOT
    # gate send_close()/send_partial_close() -- flipping this off mid-
    # position must never orphan an already-open real position; it can
    # always still be closed normally by its own exit logic. Paper is
    # completely unaffected either way, same "paper never blocked" principle
    # as the daily give-back breaker.
    real_relay_enabled: bool = True


state = EngineState()
