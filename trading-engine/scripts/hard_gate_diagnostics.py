"""Diagnoses the CURRENT hard gate (session -> MSS -> not-parabolic ->
trend-filter) directly, after three rounds of confidence-scoring changes
all landed on the exact same 17-trades/60-days result -- proving the
confidence threshold was never the actual bottleneck. This checks each
remaining hard-gate stage in isolation to find which one actually is.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from config import PAIR_CALIBRATION, PAIRS
from engine.backtester import _fetch_symbol_frames, WARMUP_BARS
from engine.entry import _in_session
from engine.macro_filter import calendar
from indicators.indicators import compute_all, market_structure_shift


async def diagnose(symbol: str, days: int = 60) -> dict:
    calib = PAIR_CALIBRATION[symbol]
    frames = await _fetch_symbol_frames(symbol, days)
    df15_full = frames["15m"]
    df4h_full = frames["4h"]
    if df15_full is None or len(df15_full) < WARMUP_BARS + 10:
        return {"symbol": symbol, "error": "insufficient data"}

    df15_full = compute_all(df15_full)
    df15_full = market_structure_shift(df15_full, calib.mss_lb)

    counts = {"total_bars": 0, "session_open": 0, "mss_confirmed": 0, "not_parabolic": 0, "trend_filter_ok": 0}

    for idx in range(WARMUP_BARS, len(df15_full)):
        ts = df15_full.index[idx]
        counts["total_bars"] += 1
        now = ts.to_pydatetime()

        if not _in_session(symbol, now):
            continue
        counts["session_open"] += 1

        last = df15_full.iloc[idx]
        mss_bullish, mss_bearish = bool(last["mss_bullish"]), bool(last["mss_bearish"])
        if mss_bullish:
            direction = "bullish"
        elif mss_bearish:
            direction = "bearish"
        else:
            continue
        counts["mss_confirmed"] += 1

        pos4h = df4h_full.index.searchsorted(ts, side="right")
        w4h = df4h_full.iloc[max(0, pos4h - 300):pos4h]
        if len(w4h) < 55:
            continue
        ema4h = w4h["close"].ewm(span=50, adjust=False).mean()
        close4h, ema4h_now = w4h["close"].iloc[-1], ema4h.iloc[-1]
        dist4h_pct = ((close4h - ema4h_now) / ema4h_now) * 100.0 if ema4h_now else 0.0
        is_para_bull, is_para_bear = dist4h_pct > calib.para_thresh, dist4h_pct < -calib.para_thresh
        if direction == "bullish" and is_para_bear:
            continue
        if direction == "bearish" and is_para_bull:
            continue
        counts["not_parabolic"] += 1

        if calib.use_trend_filter:
            mss_col = "mss_bullish" if direction == "bullish" else "mss_bearish"
            if idx < WARMUP_BARS + 3 or not bool(df15_full[mss_col].iloc[idx - 3:idx].any()):
                continue
        counts["trend_filter_ok"] += 1

    return {"symbol": symbol, "use_trend_filter": calib.use_trend_filter, "mss_lb": calib.mss_lb, **counts}


async def main():
    results = []
    for symbol in PAIRS:
        r = await diagnose(symbol, days=60)
        results.append(r)
        print(r)

    total_session = sum(r.get("session_open", 0) for r in results)
    total_mss = sum(r.get("mss_confirmed", 0) for r in results)
    total_para = sum(r.get("not_parabolic", 0) for r in results)
    total_final = sum(r.get("trend_filter_ok", 0) for r in results)
    print(f"\n=== TOTALS across {len(results)} pairs ===")
    print(f"session_open={total_session} mss_confirmed={total_mss} not_parabolic={total_para} trend_filter_ok={total_final}")


if __name__ == "__main__":
    asyncio.run(main())
