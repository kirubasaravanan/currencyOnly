"""Multi-timeframe trend alignment — symbol-agnostic, no I/O."""

from __future__ import annotations

from typing import Dict

import pandas as pd


def tf_bias(df: pd.DataFrame) -> str:
    if df is None or len(df) < 55:
        return "unknown"
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    ema50 = df["close"].ewm(span=50, adjust=False).mean()
    close, e20, e50 = df["close"].iloc[-1], ema20.iloc[-1], ema50.iloc[-1]
    if close > e20 > e50:
        return "bullish"
    if close < e20 < e50:
        return "bearish"
    return "neutral"


def mtf_alignment(frames: Dict[str, pd.DataFrame]) -> Dict:
    biases = {tf: tf_bias(df) for tf, df in frames.items()}
    total = len(biases)
    bullish = sum(1 for b in biases.values() if b == "bullish")
    bearish = sum(1 for b in biases.values() if b == "bearish")

    if bullish >= 3 and bearish == 0:
        return {"biases": biases, "aligned": True, "direction": "bullish", "alignment_score": bullish / total}
    if bearish >= 3 and bullish == 0:
        return {"biases": biases, "aligned": True, "direction": "bearish", "alignment_score": bearish / total}
    if bullish > bearish:
        return {"biases": biases, "aligned": False, "direction": "bullish", "alignment_score": bullish / total if total else 0.0}
    if bearish > bullish:
        return {"biases": biases, "aligned": False, "direction": "bearish", "alignment_score": bearish / total if total else 0.0}
    return {"biases": biases, "aligned": False, "direction": "neutral", "alignment_score": 0.0}
