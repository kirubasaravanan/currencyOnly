"""One-off diagnostic (not part of the app): walks real 15m history and
tallies how many bars pass each stage of the hybrid gate, to find which
stage is actually the bottleneck behind a low trade count. Reuses the
exact same tested helper functions as entry.py — doesn't reimplement any
logic, just counts where bars fall out.
"""
import asyncio
import sys
from datetime import timedelta

sys.path.insert(0, ".")

from config import PAIR_CALIBRATION
from engine.backtester import _fetch_symbol_frames, _window, WARMUP_BARS
from engine.entry import _in_session, _find_recent_sweep
from engine.macro_filter import calendar
from indicators.indicators import compute_all, market_structure_shift


async def diagnose(symbol: str, days: int = 60) -> None:
    calib = PAIR_CALIBRATION[symbol]
    frames = await _fetch_symbol_frames(symbol, days)
    df15_full = frames["15m"]
    df1h_full = frames["1h"]
    df4h_full = frames["4h"]

    counts = {
        "total_bars": 0,
        "session_open": 0,
        "sweep_found": 0,
        "sweep_mss_agree": 0,
        "ema1h_trend_ok": 0,
        "not_parabolic": 0,
        "trend_filter_ok": 0,
        "reached_layer2": 0,
    }

    for idx in range(WARMUP_BARS, len(df15_full)):
        ts = df15_full.index[idx]
        counts["total_bars"] += 1
        now = ts.to_pydatetime()

        if not _in_session(symbol, now):
            continue
        counts["session_open"] += 1

        w15 = df15_full.iloc[max(0, idx - 300):idx + 1]
        if len(w15) < WARMUP_BARS:
            continue
        w15c = compute_all(w15)
        w15c = market_structure_shift(w15c, calib.mss_lb)

        from engine.liquidity_pools import compute_liquidity_pools
        w1d = _window(frames["1d"], ts, max_bars=60)
        pools = compute_liquidity_pools(symbol, w15c, w1d)
        sweep = _find_recent_sweep(w15c, pools, calib.sweep_mem)
        if sweep is None:
            continue
        counts["sweep_found"] += 1

        last = w15c.iloc[-1]
        mss_bullish, mss_bearish = bool(last["mss_bullish"]), bool(last["mss_bearish"])
        if sweep["direction"] == "bullish" and mss_bullish:
            direction = "bullish"
        elif sweep["direction"] == "bearish" and mss_bearish:
            direction = "bearish"
        else:
            continue
        counts["sweep_mss_agree"] += 1

        w1h = _window(df1h_full, ts)
        if len(w1h) < 55:
            continue
        ema1h = w1h["close"].ewm(span=50, adjust=False).mean()
        close1h, e_now, e_prev = w1h["close"].iloc[-1], ema1h.iloc[-1], ema1h.iloc[-2]
        if direction == "bullish" and not (close1h > e_now and e_now > e_prev):
            continue
        if direction == "bearish" and not (close1h < e_now and e_now < e_prev):
            continue
        counts["ema1h_trend_ok"] += 1

        w4h = _window(df4h_full, ts)
        if len(w4h) < 55:
            continue
        ema4h = w4h["close"].ewm(span=50, adjust=False).mean()
        close4h, e4_now = w4h["close"].iloc[-1], ema4h.iloc[-1]
        dist4h = ((close4h - e4_now) / e4_now) * 100.0 if e4_now else 0.0
        if direction == "bullish" and dist4h < -calib.para_thresh:
            continue
        if direction == "bearish" and dist4h > calib.para_thresh:
            continue
        counts["not_parabolic"] += 1

        if calib.use_trend_filter:
            mss_col = "mss_bullish" if direction == "bullish" else "mss_bearish"
            if len(w15c) < 3 or not bool(w15c[mss_col].iloc[-3]):
                continue
        counts["trend_filter_ok"] += 1
        counts["reached_layer2"] += 1

    print(f"\n=== {symbol} (use_trend_filter={calib.use_trend_filter}, sweep_lb={calib.sweep_lb}, mss_lb={calib.mss_lb}) ===")
    for k, v in counts.items():
        pct = f"{100*v/counts['total_bars']:.2f}%" if counts["total_bars"] else "n/a"
        print(f"  {k:20s} {v:6d}   ({pct} of all bars)")


async def main():
    for symbol in ["EURUSD", "GBPUSD", "AUDUSD", "USDCAD"]:
        await diagnose(symbol, days=60)


if __name__ == "__main__":
    asyncio.run(main())
