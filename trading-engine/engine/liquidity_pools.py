"""Liquidity pool detection: equal highs/lows, prior day/week high/low,
round-number levels. Richer than V109's raw N-bar-lookback sweep, so this
is used as the sweep component of the hybrid gate's Layer 1 hard gate.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from indicators.indicators import swing_points

EQUAL_LEVEL_TOLERANCE_PCT = 0.0005
MIN_TOUCHES_FOR_EQUAL_LEVEL = 2


def _round_number_step(symbol: str) -> float:
    return 0.5 if "JPY" in symbol else 0.0050  # 50 pips for non-JPY pairs


def _cluster_equal_levels(series: pd.Series, tolerance_pct: float) -> List[Dict]:
    values = sorted(v for v in series.dropna().tolist() if v > 0)
    clusters: List[List[float]] = []
    for v in values:
        placed = False
        for cluster in clusters:
            if abs(v - cluster[-1]) / cluster[-1] <= tolerance_pct:
                cluster.append(v)
                placed = True
                break
        if not placed:
            clusters.append([v])
    return [
        {"level": sum(c) / len(c), "touches": len(c)}
        for c in clusters if len(c) >= MIN_TOUCHES_FOR_EQUAL_LEVEL
    ]


def compute_liquidity_pools(symbol: str, df_15m: Optional[pd.DataFrame], df_1d: Optional[pd.DataFrame] = None) -> List[Dict]:
    pools: List[Dict] = []

    if df_15m is not None and len(df_15m) > 10:
        sw = swing_points(df_15m, lookback=3)
        for c in _cluster_equal_levels(sw.loc[sw["swing_high"], "high"], EQUAL_LEVEL_TOLERANCE_PCT):
            pools.append({"type": "equal_high", "level": c["level"], "touches": c["touches"]})
        for c in _cluster_equal_levels(sw.loc[sw["swing_low"], "low"], EQUAL_LEVEL_TOLERANCE_PCT):
            pools.append({"type": "equal_low", "level": c["level"], "touches": c["touches"]})

        step = _round_number_step(symbol)
        if step > 0:
            current_price = float(df_15m["close"].iloc[-1])
            nearest = round(current_price / step) * step
            for mult in (-1, 0, 1):
                pools.append({"type": "round_number", "level": nearest + mult * step, "touches": 1})

    if df_1d is not None and len(df_1d) >= 2:
        pools.append({"type": "pdh", "level": float(df_1d["high"].iloc[-2]), "touches": 1})
        pools.append({"type": "pdl", "level": float(df_1d["low"].iloc[-2]), "touches": 1})
    if df_1d is not None and len(df_1d) >= 6:
        last_week = df_1d.iloc[-6:-1]
        pools.append({"type": "pwh", "level": float(last_week["high"].max()), "touches": 1})
        pools.append({"type": "pwl", "level": float(last_week["low"].min()), "touches": 1})

    return pools


def find_swept_pool(df_entry: pd.DataFrame, pools: List[Dict]) -> Optional[Dict]:
    """Checks whether the last CLOSED bar wicked through a pool level and
    closed back on the other side. Returns the most-touched (strongest)
    sweep found, implying the level acted as a reversal trigger."""
    if df_entry is None or len(df_entry) < 2 or not pools:
        return None
    last = df_entry.iloc[-1]
    swept: List[Dict] = []
    for pool in pools:
        level = pool["level"]
        if last["low"] < level <= last["close"]:  # swept down, closed back above
            swept.append({**pool, "direction": "bullish", "depth": level - float(last["low"])})
        elif last["high"] > level >= last["close"]:  # swept up, closed back below
            swept.append({**pool, "direction": "bearish", "depth": float(last["high"]) - level})
    if not swept:
        return None
    return max(swept, key=lambda p: p["touches"])
