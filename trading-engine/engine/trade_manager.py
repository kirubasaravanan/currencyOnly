"""Per-pair session close (shared by the live orchestrator and the
backtester — the same function, so both agree on exactly the same close
time; the existing Forex/Forex app has a live/backtest EOD-hour drift bug
this avoids by construction) and cooldown-after-exit tracking.

SL/TP/trailing-stop checks themselves live in paper_broker.mark_to_market()
— this module only handles the things that need `now` plus per-pair
session context the broker itself doesn't carry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from config import PAIR_CALIBRATION, COOLDOWN_BARS, ENTRY_TIMEFRAME_MINUTES, GLOBAL_SESSION_CUTOFF_MINUTES

COOLDOWN_MINUTES = COOLDOWN_BARS * ENTRY_TIMEFRAME_MINUTES
IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_minutes_of_day(now: datetime) -> int:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ist = now.astimezone(timezone.utc) + IST_OFFSET
    return ist.hour * 60 + ist.minute


def is_session_close(symbol: str, now: datetime) -> bool:
    """True once `now` (IST) is past the end of every session window
    configured for this pair today, OR past the global 22:00 IST cutoff
    (see config.GLOBAL_SESSION_CUTOFF_MINUTES) -- whichever comes first.
    Either way, no session left to trade, so any open position in this
    symbol should be force-closed."""
    minutes = _ist_minutes_of_day(now)
    if minutes >= GLOBAL_SESSION_CUTOFF_MINUTES:
        return True
    windows = PAIR_CALIBRATION[symbol].session_windows_ist
    last_window_end = max(end for _, end in windows)
    return minutes >= last_window_end


def in_cooldown(symbol: str, now: datetime, closed_trades: List[Dict]) -> bool:
    last_exit = None
    for t in closed_trades:
        if t.get("symbol") != symbol or not t.get("closed_at"):
            continue
        closed_at = datetime.fromisoformat(t["closed_at"])
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        if last_exit is None or closed_at > last_exit:
            last_exit = closed_at
    if last_exit is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elapsed_minutes = (now - last_exit).total_seconds() / 60.0
    return elapsed_minutes < COOLDOWN_MINUTES


def manage_open_positions(broker, prices: Dict[str, float], now: datetime) -> None:
    for t in list(broker.open_positions):
        if is_session_close(t["symbol"], now):
            price = prices.get(t["symbol"], t["entry_price"])
            broker.close_trade(t["id"], price, "session_close", prices)
    broker.mark_to_market(prices)
