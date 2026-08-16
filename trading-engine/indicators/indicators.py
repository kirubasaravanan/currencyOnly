"""Pure pandas/numpy technical indicators. No gold/FX branching — every
function here is symbol-agnostic; expects a DataFrame with
open/high/low/close(/volume) columns.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def ema_stack(df: pd.DataFrame, periods=(20, 50, 200)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"ema{p}"] = ema(out["close"], p)
    return out


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def _true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = _true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high, low = df["high"], df["low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr_val = atr(df, period)  # Wilder-smoothed true range
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_val.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_val.replace(0, np.nan))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx_val = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    out = df.copy()
    out["+DI"] = plus_di.fillna(0.0)
    out["-DI"] = minus_di.fillna(0.0)
    out["ADX"] = adx_val.fillna(0.0)
    return out


def choppiness(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = _true_range(df)
    atr_sum = tr.rolling(period).sum()
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    rng = (hh - ll).replace(0, np.nan)
    chop = 100 * np.log10(atr_sum / rng) / np.log10(period)
    return chop.fillna(50.0)


def swing_points(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    high, low = df["high"], df["low"]
    is_high = pd.Series(True, index=df.index)
    is_low = pd.Series(True, index=df.index)
    for shift in list(range(1, lookback + 1)) + list(range(-lookback, 0)):
        is_high &= high > high.shift(shift)
        is_low &= low < low.shift(shift)
    out = df.copy()
    out["swing_high"] = is_high.fillna(False) & high.notna()
    out["swing_low"] = is_low.fillna(False) & low.notna()
    return out


def market_structure(df: pd.DataFrame, lookback: int = 3, confirm_bars: int = 3) -> Dict:
    """Classifies recent swing sequence as bullish (HH/HL), bearish
    (LH/LL), or ranging — a state, not a trigger (see market_structure_shift
    for the break-of-structure trigger)."""
    sw = swing_points(df, lookback)
    highs = sw.loc[sw["swing_high"], "high"]
    lows = sw.loc[sw["swing_low"], "low"]
    structure = "unknown"
    if len(highs) >= 2 and len(lows) >= 2:
        higher_high = highs.iloc[-1] > highs.iloc[-2]
        higher_low = lows.iloc[-1] > lows.iloc[-2]
        lower_high = highs.iloc[-1] < highs.iloc[-2]
        lower_low = lows.iloc[-1] < lows.iloc[-2]
        if higher_high and higher_low:
            structure = "bullish"
        elif lower_high and lower_low:
            structure = "bearish"
        else:
            structure = "ranging"
    return {
        "structure": structure,
        "last_swing_high": float(highs.iloc[-1]) if len(highs) else None,
        "last_swing_low": float(lows.iloc[-1]) if len(lows) else None,
        "prior_swing_high": float(highs.iloc[-2]) if len(highs) >= 2 else None,
        "prior_swing_low": float(lows.iloc[-2]) if len(lows) >= 2 else None,
    }


def market_structure_shift(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """V109's MSS trigger: bullish when a bar closes above the highest high
    of the prior `lookback` bars, bearish when it closes below the lowest
    low — a break-of-structure event, distinct from market_structure()'s
    swing-state classification."""
    prior_high = df["high"].shift(1).rolling(lookback).max()
    prior_low = df["low"].shift(1).rolling(lookback).min()
    out = df.copy()
    out["mss_bullish"] = df["close"] > prior_high
    out["mss_bearish"] = df["close"] < prior_low
    return out


def vwap_session(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    # OANDA candle volume is tick count, not real notional — falls back to
    # 1.0 when zero/missing so VWAP degrades to a plain typical-price
    # average instead of a division by zero.
    vol = df.get("volume", pd.Series(1.0, index=df.index)).replace(0, np.nan).fillna(1.0)
    idx = df.index
    day = idx.tz_convert("UTC").date if getattr(idx, "tz", None) is not None else idx.date
    day_key = pd.Series(day, index=df.index)
    cum_pv = (typical * vol).groupby(day_key).cumsum()
    cum_v = vol.groupby(day_key).cumsum()
    return (cum_pv / cum_v).fillna(typical)


def fair_value_gaps(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    d = df.tail(lookback + 2)
    highs, lows, idx = d["high"].values, d["low"].values, d.index
    gaps: List[Dict] = []
    for i in range(2, len(d)):
        if lows[i] > highs[i - 2]:
            gaps.append({"type": "bullish", "top": float(lows[i]), "bottom": float(highs[i - 2]), "bar_time": str(idx[i - 1])})
        if highs[i] < lows[i - 2]:
            gaps.append({"type": "bearish", "top": float(lows[i - 2]), "bottom": float(highs[i]), "bar_time": str(idx[i - 1])})
    return gaps


def order_blocks(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    d = df.tail(lookback + 3)
    o, h, l, c = d["open"].values, d["high"].values, d["low"].values, d["close"].values
    idx = d.index
    body = np.abs(c - o)
    avg_body = float(np.nanmean(body)) if len(body) else 0.0
    obs: List[Dict] = []
    for i in range(1, len(d) - 1):
        if c[i] < o[i] and c[i + 1] > o[i + 1] and (c[i + 1] - o[i + 1]) > avg_body * 1.5:
            obs.append({"type": "bullish", "top": float(h[i]), "bottom": float(l[i]), "bar_time": str(idx[i])})
        if c[i] > o[i] and c[i + 1] < o[i + 1] and (o[i + 1] - c[i + 1]) > avg_body * 1.5:
            obs.append({"type": "bearish", "top": float(h[i]), "bottom": float(l[i]), "bar_time": str(idx[i])})
    return obs


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    hl2 = (df["high"] + df["low"]) / 2.0
    atr_val = atr(df, period)
    upper_basic = hl2 + multiplier * atr_val
    lower_basic = hl2 - multiplier * atr_val
    close = df["close"].values
    n = len(df)

    upper = upper_basic.to_numpy(copy=True)
    lower = lower_basic.to_numpy(copy=True)
    trend = np.ones(n, dtype=int)

    for i in range(1, n):
        upper[i] = min(upper_basic.iloc[i], upper[i - 1]) if close[i - 1] <= upper[i - 1] else upper_basic.iloc[i]
        lower[i] = max(lower_basic.iloc[i], lower[i - 1]) if close[i - 1] >= lower[i - 1] else lower_basic.iloc[i]
        if trend[i - 1] == 1 and close[i] < lower[i]:
            trend[i] = -1
        elif trend[i - 1] == -1 and close[i] > upper[i]:
            trend[i] = 1
        else:
            trend[i] = trend[i - 1]

    out = df.copy()
    out["supertrend"] = np.where(trend == 1, lower, upper)
    out["supertrend_direction"] = trend
    return out


def adr_exhaustion(daily_df: pd.DataFrame, today_high: float, today_low: float, period: int = 10) -> float:
    """V109's ADR-used-% calc: how much of the pair's average daily range
    (last `period` completed daily bars) today's range has already used.
    >100 means today has already exceeded its typical range — feeds the
    ADR-exhaustion TP/SL scaling in the risk engine."""
    if daily_df is None or len(daily_df) < 2:
        return 0.0
    avg_range = (daily_df["high"] - daily_df["low"]).tail(period).mean()
    if not avg_range or avg_range <= 0:
        return 0.0
    today_range = max(today_high - today_low, 0.0)
    return float((today_range / avg_range) * 100.0)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience: EMA20/50/200, RSI14, ADX/+DI/-DI, ATR14, Choppiness14,
    session VWAP, Supertrend — all at once."""
    out = ema_stack(df, (20, 50, 200))
    out["rsi14"] = rsi(out["close"], 14)
    adx_df = adx(out, 14)
    out["adx14"] = adx_df["ADX"]
    out["plus_di"] = adx_df["+DI"]
    out["minus_di"] = adx_df["-DI"]
    out["atr14"] = atr(out, 14)
    out["chop14"] = choppiness(out, 14)
    out["vwap"] = vwap_session(out)
    st = supertrend(out)
    out["supertrend"] = st["supertrend"]
    out["supertrend_direction"] = st["supertrend_direction"]
    return out
