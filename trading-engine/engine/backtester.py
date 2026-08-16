"""Walk-forward backtester — replays historical OANDA candles through the
exact same hybrid-gate signal engine, risk sizing, correlation filter, and
commission-aware paper broker used live. Reports gross P&L, commission
drag, and net P&L side by side so "is this actually profitable after real
costs" has a direct answer.

OANDA's 5000-candle-per-request cap (vs. Twelve Data's 500-row cap, which
the existing Forex/Forex app's own comments flag as a real historical-data
bug) still bounds how many days of 15m data a single request can cover —
`actual_days_covered` below reports the real coverage achieved so a
requested window that got silently truncated is visible, not hidden.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from config import PAIRS, ENTRY_TIMEFRAME
from data.market_data import market_data
from engine import risk, correlation, trade_manager, currency_strength
from engine.entry import entry_signal
from engine.paper_broker import PaperBroker
from engine.analytics import compute_stats

WARMUP_BARS = 60  # entry_signal() itself won't fire below this on 15m
CONTEXT_WINDOW_BARS = 300  # bounded history window passed per bar — keeps
                            # per-bar indicator recompute cost constant
                            # instead of growing with total backtest length


class BacktestBroker(PaperBroker):
    """Same P&L/commission/trailing-stop math as the live broker, but never
    touches the live broker's JSON state file and stamps trade timestamps
    with simulated time instead of wall-clock time.

    [FIX 2026-08-16] open_trade/close_trade originally only stamped
    simulated time when the CALLER explicitly passed `now=`. That covered
    the direct broker.open_trade(..., now=now) call in the scan loop below,
    but SL/TP/session-close exits reach close_trade two other ways this
    class doesn't override: trade_manager.manage_open_positions()'s own
    session-close call, and PaperBroker.mark_to_market() (unoverridden,
    inherited as-is)'s internal close_trade() calls for stop_loss/
    take_profit/take_profit_runner/break_even -- neither passes `now`, so
    every one of those exits was falling through to the base class's
    datetime.now(timezone.utc) -- REAL wall-clock time, not the simulated
    backtest date. Found by directly testing a synthetic trade: it closed
    correctly within 3 simulated hours via session_close, but was stamped
    with today's real date -- making every prior backtest's trade-duration
    data look like 1000+ hour holds when the underlying close logic was
    actually working correctly and promptly the whole time. set_sim_time()
    is called once per bar in the scan loop below; open_trade/close_trade
    now fall back to it whenever the caller doesn't pass an explicit `now`,
    so EVERY close path (not just the ones this class explicitly wraps)
    gets stamped correctly without needing to also override
    mark_to_market()."""

    def __init__(self) -> None:
        super().__init__()
        self._sim_now: Optional[datetime] = None

    def _load(self) -> None:
        pass

    def _save(self) -> None:
        pass

    def set_sim_time(self, now: datetime) -> None:
        self._sim_now = now

    def open_trade(self, signal: Dict, lots: float, now: Optional[datetime] = None) -> Optional[Dict]:
        trade = super().open_trade(signal, lots)
        effective_now = now or self._sim_now
        if trade and effective_now is not None:
            trade["opened_at"] = effective_now.isoformat()
        return trade

    def close_trade(self, trade_id: int, exit_price: float, reason: str = "manual",
                     prices: Optional[Dict[str, float]] = None, now: Optional[datetime] = None) -> Optional[Dict]:
        trade = super().close_trade(trade_id, exit_price, reason, prices)
        effective_now = now or self._sim_now
        if trade and effective_now is not None:
            trade["closed_at"] = effective_now.isoformat()
        return trade


async def _fetch_symbol_frames(symbol: str, days: int) -> Dict[str, pd.DataFrame]:
    bars_15m = min(days * 96 + CONTEXT_WINDOW_BARS, 5000)
    bars_1h = min(days * 24 + CONTEXT_WINDOW_BARS, 5000)
    bars_4h = min(days * 6 + CONTEXT_WINDOW_BARS, 5000)
    bars_1d = min(days + 60, 5000)
    # OANDA's 5000-candle-per-request cap means 5m data can cover at most
    # ~17 days in one fetch (288 bars/day) -- for backtests longer than
    # that, the TRIGGER_TIMEFRAME path only has 5m history for the most
    # recent ~17 days; earlier bars fail entry_signal's own 5m-availability
    # check and fall through as "no signal" rather than crashing.
    bars_5m = min(days * 288 + CONTEXT_WINDOW_BARS, 5000)
    return {
        "15m": await market_data.fetch(symbol, "15m", bars=bars_15m),
        "1h": await market_data.fetch(symbol, "1h", bars=bars_1h),
        "4h": await market_data.fetch(symbol, "4h", bars=bars_4h),
        "1d": await market_data.fetch(symbol, "1d", bars=bars_1d),
        "5m": await market_data.fetch(symbol, "5m", bars=bars_5m),
    }


def _window(df: pd.DataFrame, ts, max_bars: int = CONTEXT_WINDOW_BARS) -> pd.DataFrame:
    pos = df.index.searchsorted(ts, side="right")
    return df.iloc[max(0, pos - max_bars):pos]


async def run_backtest(
    symbols: Optional[List[str]] = None,
    days: int = 30,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> Dict:
    symbols = symbols or PAIRS
    frames_by_symbol: Dict[str, Dict[str, pd.DataFrame]] = {}
    for symbol in symbols:
        frames_by_symbol[symbol] = await _fetch_symbol_frames(symbol, days)

    all_times = sorted(set().union(*[
        set(f["15m"].index) for f in frames_by_symbol.values() if not f["15m"].empty
    ])) if frames_by_symbol else []

    if not all_times:
        return {"error": "no_data", "symbols": symbols, "days": days}

    actual_days_covered = round((all_times[-1] - all_times[0]).total_seconds() / 86400.0, 1)
    coverage_warning = actual_days_covered < days * 0.8

    broker = BacktestBroker()
    broker.reset()
    total_bars = len(all_times)

    for idx, ts in enumerate(all_times):
        if idx < WARMUP_BARS:
            continue
        now = ts.to_pydatetime()

        prices: Dict[str, float] = {}
        for symbol, frames in frames_by_symbol.items():
            w = _window(frames["15m"], ts, max_bars=1)
            if not w.empty:
                prices[symbol] = float(w["close"].iloc[-1])
        if not prices:
            continue

        broker.set_sim_time(now)
        trade_manager.manage_open_positions(broker, prices, now)

        w1h_by_symbol = {s: _window(f["1h"], ts) for s, f in frames_by_symbol.items()}
        ranking = currency_strength.compute_ranking(w1h_by_symbol)

        open_symbols = {t["symbol"] for t in broker.open_positions}
        for symbol, frames in frames_by_symbol.items():
            if symbol in open_symbols or symbol not in prices:
                continue
            if trade_manager.in_cooldown(symbol, now, broker.closed_trades):
                continue

            risk_check = risk.can_open_new_trade(broker.open_positions, broker.closed_trades, broker.equity, broker.peak_equity)
            if not risk_check["allowed"]:
                continue

            w15 = _window(frames["15m"], ts)
            if len(w15) < WARMUP_BARS:
                continue
            w1h = w1h_by_symbol[symbol]
            w4h = _window(frames["4h"], ts)
            w1d = _window(frames["1d"], ts, max_bars=60)
            w5m = _window(frames["5m"], ts, max_bars=CONTEXT_WINDOW_BARS * 3)
            if len(w1h) < 55 or len(w4h) < 55:
                continue

            signal = entry_signal(symbol, {"15m": w15, "1h": w1h, "4h": w4h, "1d": w1d, "5m": w5m}, now=now, currency_ranking=ranking)
            if signal is None:
                continue

            exposure = correlation.would_exceed_exposure(symbol, signal["side"], broker.open_positions)
            if exposure["blocked"]:
                continue

            sizing = risk.position_size(symbol, broker.equity, signal["entry_price"], signal["sl_price"], signal.get("size_multiplier", 1.0), prices)
            broker.open_trade(signal, sizing["lots"], now=now)
            open_symbols.add(symbol)

        if progress_cb and idx % 200 == 0:
            progress_cb(idx / total_bars)

    # Force-close anything still open at the end of the window so P&L is
    # fully realized rather than leaving marked-to-market phantom trades.
    final_prices = {s: float(f["15m"]["close"].iloc[-1]) for s, f in frames_by_symbol.items() if not f["15m"].empty}
    for t in list(broker.open_positions):
        price = final_prices.get(t["symbol"], t["entry_price"])
        broker.close_trade(t["id"], price, "backtest_end", final_prices, now=all_times[-1].to_pydatetime())

    if progress_cb:
        progress_cb(1.0)

    stats = compute_stats(broker)
    return {
        "symbols": symbols,
        "days": days,
        "entry_timeframe": ENTRY_TIMEFRAME,
        "bars_processed": total_bars - WARMUP_BARS,
        "actual_days_covered": actual_days_covered,
        "data_coverage_warning": coverage_warning,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "closed_trades": broker.closed_trades,
    }
