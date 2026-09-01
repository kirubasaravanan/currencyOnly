"""Hybrid-gate entry signal engine.

Hard gate (V109-faithful — only what V109 itself treats as a hard AND
condition): session window, news blackout, market-structure-shift
confirmed, not parabolically extended against the candidate direction,
and (for applicable pairs) the trend-filter confirmation.

[FIX 2026-08-16] An earlier version also made sweep and 1H-EMA-trend+slope
into additional hard gates, then bolted on a SEPARATE second 8-factor
weighted score (a "Layer 2") that had to independently clear its own
threshold. V109 itself only has two true hard conditions (mssL/mssH, not
isParaBull/isParaBear) — sweep, EMA1H, EMA4H, ADX, daily bias, and
volatility expansion are all WEIGHTED, partial-credit factors inside its
own single confidence score (pBuy/pSell), not separate hard requirements.
Stacking "V109's hard gate" + "V109's weighted factors re-added as MORE
hard gates" + "an entirely new second weighted gate" compounded three
probability filters where V109 has one — empirically found to explain a
~60x trade-frequency gap versus V109's own live behavior (found via direct
user comparison, not a diagnostic script). Rebuilt below as V109's actual
structure: a small hard gate, and ONE weighted score combining V109's own
7 factors (its real weights) with this app's additions (session-currency
dominance, currency-strength, RSI, VWAP, supertrend, OB/FVG).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pandas as pd

import config
from config import PAIR_CALIBRATION, PIP_SIZE
from indicators.indicators import (
    compute_all,
    market_structure_shift,
    adr_exhaustion,
    fair_value_gaps,
    order_blocks,
    atr as atr_indicator,
)
from engine.liquidity_pools import compute_liquidity_pools
from engine.mtf_trend import mtf_alignment
from engine.session_dominance import dominance_score
from engine.currency_strength import strength_factor
from engine.macro_filter import calendar

IST_OFFSET_MINUTES = 5 * 60 + 30

ADR_EXHAUSTION_THRESHOLD_PCT = 70.0
ADR_STOP_SCALE = 1.00
ADR_TP_SCALE = 0.70
ADR_PERIOD = 10

ADX_TREND_THRESHOLD = 20.0
RSI_BULL, RSI_BEAR = 55.0, 45.0
CHOP_THRESHOLD = 50.0
ZONE_PROXIMITY_ATR_MULT = 1.0

# [FIX 2026-08-16, round 2] Unifying everything into one blended score
# (round 1 of this fix) still under-produced trades versus V109's own real
# frequency: V109's 7 factors only make up ~25% of that blended score's
# total weight, so even a PERFECT V109 setup needed this app's 7 additions
# to also substantially agree to clear the same 65% bar -- the same
# compounding problem, just hidden inside one averaged number instead of
# two separate gates (confirmed: 8/17 trades in that version had
# byte-identical P&L to the pre-fix version -- the same handful of
# overwhelming setups pass regardless of restructuring, meaning the real
# bottleneck hadn't actually moved).
#
# V109_WEIGHTS is V109's own real pBuy/pSell (its own 7 factors, its own
# relative weights, summed to 1.0 including MSS's 0.25 even though MSS is
# also a hard gate below -- matching V109's own script, which double-
# counts it too) — this is the ONLY thing gated against
# config.state.confidence_threshold, so it reproduces V109's own real
# trade frequency, not a diluted version of it.
V109_WEIGHTS = {
    "mss_confirmed": 0.25,  # always 1.0 here -- already required by the hard gate above
    "ema1h_trend_slope": 0.25,
    "ema4h_bias": 0.15,
    "sweep_recent": 0.15,
    "adx_trending": 0.10,
    "daily_bias": 0.05,
    "vol_expanding": 0.05,
}
V109_WEIGHT_SUM = sum(V109_WEIGHTS.values())

# This app's additions no longer gate entry at all -- they scale POSITION
# SIZE (see risk.py's sizing call in orchestrator.py/backtester.py, which
# multiplies by this score) so a qualifying V109 signal always trades
# (matching your live V109 experience), but sizes up on strong confluence
# agreement and down when this app's added signals disagree, instead of
# silently suppressing volume.
CONFLUENCE_WEIGHTS = {
    "mtf_alignment": 1.0,
    "rsi_direction": 0.5,
    "vwap_confirm": 0.4,
    "supertrend": 0.3,
    "ob_fvg_bonus": 0.3,
    "session_dominance": 0.6,
    "currency_strength": 0.6,
}
CHOPPINESS_PENALTY_WEIGHT = 0.5
CONFLUENCE_WEIGHT_SUM = sum(CONFLUENCE_WEIGHTS.values())


def _ist_minutes_of_day(now: datetime) -> int:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ist = now.astimezone(timezone.utc) + timedelta(minutes=IST_OFFSET_MINUTES)
    return ist.hour * 60 + ist.minute


def _in_session(symbol: str, now: datetime) -> bool:
    minutes = _ist_minutes_of_day(now)
    if minutes >= config.GLOBAL_SESSION_CUTOFF_MINUTES:
        return False
    for start, end in PAIR_CALIBRATION[symbol].session_windows_ist:
        if start <= minutes < end:
            return True
    return False


# [ADD 2026-08-16, explicit user instruction] No new entries within this
# many minutes of the pair's own session-close deadline (trade_manager's
# force-close, tied to the LAST window's end each day -- see
# is_session_close there). Empirically confirmed on real backtest data:
# trades opened 30-60 min before forced closure had negative average P&L
# (-$14.66 and -$7.51 avg) and got force-closed 88-100% of the time,
# vs. +$6.44 avg / 20.1% forced-close for trades with normal room to
# develop -- they never get time to reach SL/TP naturally, just wasted
# spread/commission. Matches the identical fix already proven on the
# sister Forex app's real-money accounts (30-min version there). 45 min
# chosen over 30, per explicit user reasoning: FX pairs develop moves
# more gradually across a session than gold's near-continuous liquidity.
#
# Only applies relative to the LAST window's end (where forced closure
# actually happens) -- entries near an EARLIER window's end aren't cut,
# since a position opened there just keeps riding into the next window
# rather than being force-closed (see trade_manager.is_session_close's
# own docstring for why only the last window's end matters for closure).
#
# Edge case: AUDCAD's second window (16:30-17:00 IST) is only 30 minutes
# long -- shorter than this 45-minute cutoff -- so it gets zero new
# entries in that window entirely (its first window, 11:00-14:30, is
# unaffected). Existing positions can still ride into and force-close at
# 17:00 as normal; only new entries in that narrow slot are removed.
ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE = 45


def _entry_cutoff_reached(symbol: str, now: datetime) -> bool:
    minutes = _ist_minutes_of_day(now)
    last_window_end = max(end for _, end in PAIR_CALIBRATION[symbol].session_windows_ist)
    return minutes >= last_window_end - ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE


def _find_recent_sweep(df_15m: pd.DataFrame, pools, sweep_mem: int) -> Optional[Dict]:
    """Scans the last `sweep_mem` bars (not just the latest one) for a
    liquidity sweep, keeping the most recent match — approximates V109's
    'bars since sweep <= sweepMem' recency window. Now a WEIGHTED factor
    (V109's memL/memH, weight 0.15), not a prerequisite for a candidate to
    exist at all."""
    if df_15m is None or len(df_15m) < 2 or not pools:
        return None
    window = df_15m.iloc[-sweep_mem:] if len(df_15m) > sweep_mem else df_15m
    most_recent = None
    for i in range(len(window)):
        bar = window.iloc[i]
        for pool in pools:
            level = pool["level"]
            if bar["low"] < level <= bar["close"]:
                most_recent = {**pool, "direction": "bullish", "bars_ago": len(window) - 1 - i}
            elif bar["high"] > level >= bar["close"]:
                most_recent = {**pool, "direction": "bearish", "bars_ago": len(window) - 1 - i}
    return most_recent


STRUCTURAL_BUFFER_ATR_MULT = 0.15


def _structural_sl_tp(symbol: str, direction: str, entry_price: float, atr_val: float, clamped_sl_dist: float,
                       tp1_dist: float, max_sl_pips: float, pip: float, pools) -> Optional[Dict]:
    """Nudges the ATR-based SL/TP1 to respect nearby structural levels: a
    level breaking is what actually invalidates a trend, so SL should sit
    just BEYOND the nearest opposing level (not short of it, where
    ordinary noise could stop the trade out without the level really
    breaking) — and price tends to react AT a level rather than punch
    through it, so TP1 should sit just BEFORE the nearest same-side level
    rather than a further ATR target that may never get touched. Returns
    None if respecting the level would need a wider SL than the pair's
    max_sl_pips allows — that trade is skipped rather than taken with a
    stop that isn't actually structurally safe."""
    buffer = atr_val * STRUCTURAL_BUFFER_ATR_MULT
    is_bull = direction == "bullish"

    opposing_levels = [p["level"] for p in pools if (p["level"] < entry_price if is_bull else p["level"] > entry_price)]
    same_side_levels = [p["level"] for p in pools if (p["level"] > entry_price if is_bull else p["level"] < entry_price)]

    sl_dist = clamped_sl_dist
    if opposing_levels:
        nearest = max(opposing_levels) if is_bull else min(opposing_levels)
        level_dist = abs(entry_price - nearest)
        if level_dist > clamped_sl_dist:  # raw SL sits short of the real level -- widen past it
            widened = level_dist + buffer
            if widened / pip > max_sl_pips:
                return None  # can't respect the level within this pair's risk cap
            sl_dist = widened

    target_dist = tp1_dist
    if same_side_levels:
        nearest = min(same_side_levels) if is_bull else max(same_side_levels)
        level_dist = abs(nearest - entry_price)
        if level_dist < tp1_dist:  # a level sits before the raw ATR target -- pull TP back to just short of it
            pulled = level_dist - buffer
            # [FIX 2026-09-01, explicit user instruction, real-trade + backtest
            # evidence] Was: max(pulled, sl_dist * 0.5) for every pair -- floor-
            # clamped TP1 at half the SL rather than shrinking further. Real
            # NZDUSD trades showed that floor was the binding constraint often
            # enough (3/6 recent trades landed at exactly rr=0.5) to be
            # negative-EV there: a full SL loses 2x what a full TP1 win pays,
            # and NZDUSD's actual win rate (~55%) doesn't clear the ~67% bar
            # that ratio needs. Backtested skipping these trades entirely
            # instead of floor-clamping, symbol-by-symbol (47-day window,
            # 7 majors): NZDUSD flipped from -$188.73 to +$40.66 (45->35
            # trades) as hoped, BUT applied to every pair it was a net loss of
            # -$1,201.58 overall -- GBPUSD (88.9% win rate on these same
            # floor-hit setups) and USDJPY (73.9%) lost far more from skipped
            # winners than NZDUSD gained, because the geometry that triggers
            # this floor doesn't know which pair's win rate can actually
            # support a 0.5 RR. So: scoped to just the pairs proven not to
            # support it, not a blanket rule -- see
            # config.STRUCTURAL_TP_FLOOR_SKIP_PAIRS.
            if pulled < sl_dist * 0.5 and symbol in config.STRUCTURAL_TP_FLOOR_SKIP_PAIRS:
                return None
            target_dist = max(pulled, sl_dist * 0.5)

    return {"sl_dist": sl_dist, "tp1_dist": target_dist}


def _nearest_zone_factor(direction: str, close: float, atr_val: float, gaps, blocks) -> float:
    kind = "bullish" if direction == "bullish" else "bearish"
    proximity = atr_val * ZONE_PROXIMITY_ATR_MULT
    for zone in list(gaps) + list(blocks):
        if zone["type"] != kind:
            continue
        top, bottom = zone["top"], zone["bottom"]
        if bottom - proximity <= close <= top + proximity:
            return 1.0
    return 0.0


def entry_signal(
    symbol: str,
    frames: Dict[str, pd.DataFrame],
    now: Optional[datetime] = None,
    currency_ranking: Optional[Dict[str, float]] = None,
) -> Optional[Dict]:
    calib = PAIR_CALIBRATION[symbol]
    df_15m = frames.get("15m")
    df_1h = frames.get("1h")
    df_4h = frames.get("4h")
    df_1d = frames.get("1d")
    df_5m = frames.get("5m")

    if df_15m is None or len(df_15m) < 60 or df_1h is None or len(df_1h) < 55 or df_4h is None or len(df_4h) < 55:
        return None

    if now is None:
        now = df_15m.index[-1].to_pydatetime()

    # ── Hard gate (V109-faithful: session, news, MSS, parabolic, trend-filter only) ──
    if not _in_session(symbol, now):
        return None
    if _entry_cutoff_reached(symbol, now):
        return None
    if calendar.in_blackout()["blocked"]:
        return None

    df_15m = compute_all(df_15m)
    df_15m = market_structure_shift(df_15m, calib.mss_lb)
    last = df_15m.iloc[-1]

    # MSS is V109's real hard structural-break gate, evaluated on the
    # current bar directly (matching Pine's own per-bar mssL/mssH) --
    # determines candidate direction on its own, same as V109.
    mss_bullish, mss_bearish = bool(last["mss_bullish"]), bool(last["mss_bearish"])
    if mss_bullish:
        direction = "bullish"
    elif mss_bearish:
        direction = "bearish"
    else:
        return None

    ema4h = df_4h["close"].ewm(span=50, adjust=False).mean()
    close4h, ema4h_now = df_4h["close"].iloc[-1], ema4h.iloc[-1]
    dist4h_pct = ((close4h - ema4h_now) / ema4h_now) * 100.0 if ema4h_now else 0.0
    is_para_bull, is_para_bear = dist4h_pct > calib.para_thresh, dist4h_pct < -calib.para_thresh
    if direction == "bullish" and is_para_bear:
        return None
    if direction == "bearish" and is_para_bull:
        return None

    if calib.use_trend_filter:
        # Approximation of V109's pairUseFilter (its own multi-bar
        # buyTrendValid check delays entry by confirming the setup 2 bars
        # later, rather than requiring a second independent trigger).
        # Checks the break has been sustained over the last few bars.
        mss_col = "mss_bullish" if direction == "bullish" else "mss_bearish"
        if len(df_15m) < 4 or not bool(df_15m[mss_col].iloc[-4:-1].any()):
            return None

    pools = compute_liquidity_pools(symbol, df_15m, df_1d)

    # Entry fill price: 5m TRIGGER timeframe when enabled (config toggle,
    # A/B-tested — see scripts/trigger_timeframe_ab.py; currently off by
    # default since an earlier version that ALSO moved MSS confirmation to
    # 5m tested significantly worse), else the 15m STRUCTURE close.
    if config.USE_TRIGGER_TIMEFRAME and df_5m is not None and len(df_5m) > 0:
        entry_price = float(df_5m["close"].iloc[-1])
    else:
        entry_price = float(last["close"])

    # ── Single weighted confidence score: V109's own 7 factors (its real
    # weights) + this app's additions, blended into ONE score — not a
    # second independent gate. ──
    ema1h = df_1h["close"].ewm(span=50, adjust=False).mean()
    close1h, ema1h_now, ema1h_prev = df_1h["close"].iloc[-1], ema1h.iloc[-1], ema1h.iloc[-2]
    ema1h_trend_ok = (
        (close1h > ema1h_now and ema1h_now > ema1h_prev) if direction == "bullish"
        else (close1h < ema1h_now and ema1h_now < ema1h_prev)
    )
    ema1h_factor = 1.0 if ema1h_trend_ok else 0.0

    ema4h_factor = 1.0 if (direction == "bullish" and close4h > ema4h_now) or (direction == "bearish" and close4h < ema4h_now) else 0.0

    sweep = _find_recent_sweep(df_15m, pools, calib.sweep_mem)
    sweep_factor = 1.0 if (sweep is not None and sweep["direction"] == direction) else 0.0

    adx_factor = 1.0 if float(last["adx14"]) > ADX_TREND_THRESHOLD else 0.0

    daily_factor = 0.5  # neutral default -- not enough daily history to judge either way
    if df_1d is not None and len(df_1d) >= 200:
        daily_ema200 = df_1d["close"].ewm(span=200, adjust=False).mean().iloc[-1]
        daily_close = df_1d["close"].iloc[-1]
        daily_factor = 1.0 if (direction == "bullish" and daily_close > daily_ema200) or (direction == "bearish" and daily_close < daily_ema200) else 0.0

    atr_fast = atr_indicator(df_15m, 7).iloc[-1]
    atr_slow = atr_indicator(df_15m, 50).iloc[-1]
    vol_expanding_factor = 1.0 if atr_fast > atr_slow else 0.0

    mtf = mtf_alignment(frames)
    mtf_factor = mtf["alignment_score"] if mtf["direction"] == direction else 0.0

    rsi_val = float(last["rsi14"])
    rsi_factor = 1.0 if (direction == "bullish" and rsi_val > RSI_BULL) or (direction == "bearish" and rsi_val < RSI_BEAR) else 0.0

    vwap_factor = 1.0 if (direction == "bullish" and last["close"] > last["vwap"]) or (direction == "bearish" and last["close"] < last["vwap"]) else 0.0

    st_dir = int(last["supertrend_direction"])
    supertrend_factor = 1.0 if (direction == "bullish" and st_dir == 1) or (direction == "bearish" and st_dir == -1) else 0.0

    gaps = fair_value_gaps(df_15m, lookback=50)
    blocks = order_blocks(df_15m, lookback=50)
    ob_fvg_factor = _nearest_zone_factor(direction, float(last["close"]), float(last["atr14"]), gaps, blocks)

    dominance = dominance_score(symbol, now)
    dominance_factor = dominance["score"]

    strength = strength_factor(symbol, direction, currency_ranking)

    chop_val = float(last["chop14"])
    chop_penalty_factor = 1.0 if chop_val > CHOP_THRESHOLD else 0.0

    factor_scores = {
        "ema1h_trend_slope": ema1h_factor,
        "ema4h_bias": ema4h_factor,
        "sweep_recent": sweep_factor,
        "adx_trending": adx_factor,
        "daily_bias": daily_factor,
        "vol_expanding": vol_expanding_factor,
        "mtf_alignment": mtf_factor,
        "rsi_direction": rsi_factor,
        "vwap_confirm": vwap_factor,
        "supertrend": supertrend_factor,
        "ob_fvg_bonus": ob_fvg_factor,
        "session_dominance": dominance_factor,
        "currency_strength": strength,
        "choppiness_penalty": chop_penalty_factor,
    }

    # V109's own gate: this is the ONLY score checked against the
    # confidence threshold, so trade frequency matches V109's real
    # behavior rather than a version diluted by this app's additions.
    v109_scores = dict(factor_scores)
    v109_scores["mss_confirmed"] = 1.0  # guaranteed true -- already required by the hard gate above
    v109_weighted_sum = sum(V109_WEIGHTS[k] * v109_scores[k] for k in V109_WEIGHTS)
    confidence = max(0.0, min(1.0, v109_weighted_sum / V109_WEIGHT_SUM))

    threshold = config.state.confidence_threshold + calib.conf_boost
    if confidence < threshold:
        return None

    # This app's additions: no longer gate entry, they scale position size
    # (0.5x-1.5x) via size_multiplier below -- see risk.py's caller, which
    # multiplies the ATR-sized lots by this. config.ENABLE_CURRENCY_STRENGTH_FACTOR
    # isolates that one factor's real effect via A/B backtest (see
    # scripts/currency_strength_ab.py) without confounding other changes.
    active_confluence_weights = dict(CONFLUENCE_WEIGHTS)
    if not config.ENABLE_CURRENCY_STRENGTH_FACTOR:
        active_confluence_weights.pop("currency_strength", None)
    confluence_weight_sum = sum(active_confluence_weights.values())

    confluence_weighted_sum = sum(active_confluence_weights[k] * factor_scores[k] for k in active_confluence_weights)
    confluence_weighted_sum -= CHOPPINESS_PENALTY_WEIGHT * chop_penalty_factor
    confluence_score = max(0.0, min(1.0, confluence_weighted_sum / confluence_weight_sum))
    size_multiplier = round(0.5 + confluence_score, 3)  # 0.5x (no confluence agreement) to 1.5x (full agreement)

    # Gate mode: global REQUIRE_CONFLUENCE_GATE, overridable per-pair via
    # config.PAIR_GATE_MODE_OVERRIDE once a pair has enough of its OWN
    # live/forward history to justify differing from the global default
    # (empty for now — see that dict's docstring for why it isn't
    # populated from the single backtest sample that first surfaced
    # per-pair differences).
    require_confluence_gate = config.PAIR_GATE_MODE_OVERRIDE.get(symbol, config.REQUIRE_CONFLUENCE_GATE)
    if require_confluence_gate and confluence_score < threshold:
        return None

    # ── SL/TP construction (ATR base, V109 multiples, ADR-exhaustion scaled) ──
    atr15 = float(last["atr14"])
    pip = PIP_SIZE[symbol]
    base_dist = max(atr15 * 2.0, calib.min_base)

    today = df_15m[df_15m.index.date == df_15m.index[-1].date()]
    adr_pct = adr_exhaustion(df_1d, float(today["high"].max()), float(today["low"].min()), ADR_PERIOD)
    is_exhausted = adr_pct >= ADR_EXHAUSTION_THRESHOLD_PCT
    active_stop_mult = calib.stop_mult * (ADR_STOP_SCALE if is_exhausted else 1.0)
    active_tp_mult = calib.tp_mult * (ADR_TP_SCALE if is_exhausted else 1.0)

    actual_sl_dist = base_dist * active_stop_mult
    sl_pips = actual_sl_dist / pip
    clamped_sl_pips = max(float(calib.min_sl_pips), min(sl_pips, float(calib.max_sl_pips)))
    clamped_sl_dist = clamped_sl_pips * pip

    tp1_dist = clamped_sl_dist * (active_tp_mult / active_stop_mult)

    structural = _structural_sl_tp(symbol, direction, entry_price, atr15, clamped_sl_dist, tp1_dist, calib.max_sl_pips, pip, pools)
    if structural is None:
        return None
    clamped_sl_dist, tp1_dist = structural["sl_dist"], structural["tp1_dist"]
    tp2_dist = tp1_dist * calib.tp2_mult

    if direction == "bullish":
        sl_price = entry_price - clamped_sl_dist
        tp_price = entry_price + tp1_dist
        tp2_price = entry_price + tp2_dist
    else:
        sl_price = entry_price + clamped_sl_dist
        tp_price = entry_price - tp1_dist
        tp2_price = entry_price - tp2_dist

    return {
        "symbol": symbol,
        "side": "BULLISH" if direction == "bullish" else "BEARISH",
        "entry_price": round(entry_price, 6),
        "sl_price": round(sl_price, 6),
        "tp_price": round(tp_price, 6),
        "tp2_price": round(tp2_price, 6),
        "rr": round(tp1_dist / clamped_sl_dist, 3) if clamped_sl_dist else 0.0,
        "confidence": round(confidence, 4),
        "threshold": round(threshold, 4),
        "confluence_score": round(confluence_score, 4),
        "size_multiplier": size_multiplier,
        "atr": round(atr15, 6),
        "rsi": round(rsi_val, 2),
        "adx": round(float(last["adx14"]), 2),
        "choppiness": round(chop_val, 2),
        "session": dominance["session"],
        "adr_used_pct": round(adr_pct, 1),
        "adr_exhausted": is_exhausted,
        "sweep": sweep,
        "mtf_alignment": mtf,
        "factor_scores": factor_scores,
        "reasons": {
            "gate": {
                "mss_bullish": mss_bullish,
                "mss_bearish": mss_bearish,
                "parabolic_blocked": False,
                "trend_filter_applied": calib.use_trend_filter,
            },
            "confluence": factor_scores,
            "session_dominance": dominance,
        },
    }
