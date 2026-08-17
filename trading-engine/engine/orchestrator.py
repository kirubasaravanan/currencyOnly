"""Live 60s scan loop: fetch -> hybrid-gate signal -> risk/correlation
checks -> paper broker -> trade management. Deliberately mirrors the
backtester's per-bar logic (same entry_signal/risk/correlation/
trade_manager calls) so live and backtest can't silently diverge.

Also fires Discord alerts (entry, exit, session summary, EOD summary) and,
if TRADESGNL_LICENSE_ID is configured in .env, relays every paper entry/
exit to a real MT5 account via TradeSgnl (engine/tradesgnl_relay.py) —
both live-only, since this module (unlike paper_broker.py) is never
touched by the backtester, so a historical replay can never trigger a
fake alert or a real order.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import PAIRS, MAJORS, MTF_TIMEFRAMES, ENGINE_LOOP_SECONDS, SESSIONS_UTC, state
from data.market_data import market_data
from engine import risk, correlation, trade_manager, currency_strength, discord_alerts, tradesgnl_relay, trade_sync_heartbeat
from engine.entry import entry_signal
from engine.paper_broker import broker
from engine.session_dominance import current_session

_task: Optional[asyncio.Task] = None
last_signals: Dict[str, Dict] = {}
last_error: Optional[str] = None

_alerted_closed_count = 0
_current_session_name: Optional[str] = None
_session_trades: List[Dict] = []
_current_ist_date: Optional[str] = None
_day_trades: List[Dict] = []

IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_date_str(now: datetime) -> str:
    return (now + IST_OFFSET).date().isoformat()


def _ist_minutes_of_day(now: datetime) -> int:
    ist = now.astimezone(timezone.utc) + IST_OFFSET
    return ist.hour * 60 + ist.minute


# [CHANGED 2026-08-17, explicit user instruction] Was triggered by the IST
# calendar date rolling over (~00:00 IST) -- a ~4h gap after every pair's
# own last session window has already closed (the latest is AUDJPY at
# 20:00 IST). Moved to fire shortly after the whole day's trading is
# actually done instead of waiting for midnight.
EOD_TRIGGER_MINUTES = 20 * 60 + 5  # 20:05 IST
_last_eod_date: Optional[str] = None


async def _process_new_closed_trades(now: datetime) -> None:
    """Diffs broker.closed_trades against the last-seen count to find newly
    closed trades since the previous scan, fires exit alerts for each, and
    buckets them into the running session/day accumulators."""
    global _alerted_closed_count
    new_trades = broker.closed_trades[_alerted_closed_count:]
    _alerted_closed_count = len(broker.closed_trades)
    for t in new_trades:
        await discord_alerts.alert_trade_closed(t)
        await tradesgnl_relay.send_close(t)
        _session_trades.append(t)
        _day_trades.append(t)


_relayed_partial_ids: set = set()


async def _process_partial_takes() -> None:
    """Detects trades that just flipped partial_taken=True (paper's own
    50%-at-TP1 mechanic — dynamic EXIT_MODE only). _take_partial() doesn't
    move a trade into closed_trades the way a full close does, so this
    can't reuse the closed-trades diff above; needs its own pass over
    open_positions."""
    for t in broker.open_positions:
        if t.get("partial_taken") and t["id"] not in _relayed_partial_ids:
            _relayed_partial_ids.add(t["id"])
            relayed = await tradesgnl_relay.send_partial_close(t)
            await discord_alerts.alert_partial_close(t, relayed)


def _majors_subset(trades: List[Dict]) -> List[Dict]:
    return [t for t in trades if t["symbol"] in MAJORS]


async def _check_session_rollover(now: datetime) -> None:
    global _current_session_name, _session_trades
    session_name = current_session(now)
    if _current_session_name is None:
        _current_session_name = session_name
        return
    if session_name != _current_session_name:
        # Two separate messages per explicit user request: majors-only
        # performance visible independently from the full 17-pair set,
        # to inform which (if any) crosses get added on top of a
        # majors-only core.
        await discord_alerts.alert_session_summary(_current_session_name, _majors_subset(_session_trades), "MAJORS")
        await discord_alerts.alert_session_summary(_current_session_name, _session_trades, "ALL 17 PAIRS")
        _current_session_name = session_name
        _session_trades = []


async def _check_eod_rollover(now: datetime) -> None:
    global _current_ist_date, _day_trades, _last_eod_date
    date_str = _ist_date_str(now)
    if _current_ist_date is None:
        _current_ist_date = date_str
        return
    if date_str != _current_ist_date:
        # New IST calendar day started -- just resets the day-trades bucket
        # for the day ahead; the actual EOD alert fires below, at 20:05 IST,
        # not at this midnight boundary.
        _current_ist_date = date_str
        _day_trades = []

    if _ist_minutes_of_day(now) >= EOD_TRIGGER_MINUTES and _last_eod_date != date_str:
        dd = _drawdown_pct()
        await discord_alerts.alert_eod_summary(date_str, _majors_subset(_day_trades), broker.equity, dd, "MAJORS")
        await discord_alerts.alert_eod_summary(date_str, _day_trades, broker.equity, dd, "ALL 17 PAIRS")
        _last_eod_date = date_str


def _drawdown_pct() -> float:
    if broker.peak_equity <= 0:
        return 0.0
    return round(((broker.peak_equity - broker.equity) / broker.peak_equity) * 100.0, 2)


_last_heartbeat_at = 0.0


async def _check_sync_heartbeat() -> None:
    """Own cadence, separate from the 60s scan loop -- MT5 reads are
    blocking, so this runs in a thread and only every few minutes, not
    every scan. See trade_sync_heartbeat.py for what this actually checks
    and why (explicit user request, 2026-08-17: "keep monitoring and let
    me know if anything else desyncs")."""
    global _last_heartbeat_at
    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - _last_heartbeat_at < trade_sync_heartbeat.HEARTBEAT_INTERVAL_SECONDS:
        return
    _last_heartbeat_at = now_ts
    result = await asyncio.to_thread(trade_sync_heartbeat.run_heartbeat)
    if result.get("disabled"):
        return
    await discord_alerts.alert_sync_heartbeat(result)


async def _scan_once() -> None:
    global last_error
    now = datetime.now(timezone.utc)
    prices: Dict[str, float] = {}
    frames_by_symbol: Dict[str, Dict] = {}

    for symbol in PAIRS:
        try:
            frames = await market_data.fetch_multi(symbol, MTF_TIMEFRAMES)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{symbol} fetch: {exc}"
            continue
        frames_by_symbol[symbol] = frames
        df15 = frames.get("15m")
        if df15 is not None and not df15.empty:
            prices[symbol] = float(df15["close"].iloc[-1])

    if not prices:
        return

    trade_manager.manage_open_positions(broker, prices, now)
    await _process_partial_takes()
    await _process_new_closed_trades(now)
    await _check_session_rollover(now)
    await _check_eod_rollover(now)
    await _check_sync_heartbeat()

    ranking = currency_strength.compute_ranking({s: f.get("1h") for s, f in frames_by_symbol.items()})

    open_symbols = {t["symbol"] for t in broker.open_positions}
    for symbol, frames in frames_by_symbol.items():
        if symbol in open_symbols or symbol not in prices:
            continue
        if trade_manager.in_cooldown(symbol, now, broker.closed_trades):
            continue

        risk_check = risk.can_open_new_trade(broker.open_positions, broker.closed_trades, broker.equity, broker.peak_equity)
        if not risk_check["allowed"]:
            continue

        try:
            signal = entry_signal(symbol, frames, now=now, currency_ranking=ranking)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{symbol} entry_signal: {exc}"
            continue

        if signal is None:
            continue
        last_signals[symbol] = signal

        exposure = correlation.would_exceed_exposure(symbol, signal["side"], broker.open_positions)
        if exposure["blocked"]:
            continue

        sizing = risk.position_size(symbol, broker.equity, signal["entry_price"], signal["sl_price"], signal.get("size_multiplier", 1.0), prices)
        trade = broker.open_trade(signal, sizing["lots"])
        if trade is not None:
            await discord_alerts.alert_trade_opened(trade)
            await tradesgnl_relay.send_entry(trade)
        open_symbols.add(symbol)


async def _loop() -> None:
    global last_error
    state.running = True
    state.started_at = datetime.now(timezone.utc).timestamp()
    await discord_alerts.alert_engine_event("🚀 currencyOnly engine started", f"{len(PAIRS)} pairs, paper trading only")
    while state.running:
        try:
            await _scan_once()
            state.last_scan_at = datetime.now(timezone.utc).timestamp()
            state.scan_count += 1
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        await asyncio.sleep(ENGINE_LOOP_SECONDS)


async def start() -> None:
    global _task, _alerted_closed_count, _day_trades, _session_trades, _current_ist_date, _current_session_name
    if _task is None or _task.done():
        # Re-arm the relay's confirmed-sent tracking for whatever's already
        # open in the paper broker -- otherwise a restart silently strands
        # those positions' closes/partials (see tradesgnl_relay.seed_confirmed_ids).
        tradesgnl_relay.seed_confirmed_ids(broker.open_positions)
        # [FIX 2026-08-17, found live -- user reported Discord spam right
        # after a restart] broker.closed_trades is loaded from persisted
        # disk state (up to the last 500 trades) on every startup, but this
        # counter is a plain in-memory int that resets to 0 -- so right
        # after a restart, _process_new_closed_trades() saw the ENTIRE
        # persisted history as "new" and re-fired a Discord close-alert for
        # every one of them at once. Seeding it to the current length means
        # only trades that close AFTER this startup count as new.
        _alerted_closed_count = len(broker.closed_trades)

        # [FIX 2026-08-17, found live -- user reported an EOD summary
        # showing all zeros despite a full day of real trades] Same root
        # pattern as the two fixes just above: _day_trades/_session_trades
        # are plain in-memory lists that only ever grow via
        # _process_new_closed_trades() as closes happen AFTER this process
        # started -- a restart during the trading day (this app restarted
        # several times today alone, e.g. to deploy the 20:05 EOD-timing
        # change itself) reset them to empty while broker.closed_trades
        # (persisted) still has the real history. The EOD/session alerts
        # then reported "0 trades" even though the day's trades genuinely
        # happened. Seed both from broker.closed_trades on startup instead
        # of trusting them to have been empty all along.
        now = datetime.now(timezone.utc)
        _current_ist_date = _ist_date_str(now)
        _day_trades = [
            t for t in broker.closed_trades
            if t.get("closed_at") and _ist_date_str(datetime.fromisoformat(t["closed_at"])) == _current_ist_date
        ]

        _current_session_name = current_session(now)
        session_hours = SESSIONS_UTC.get(_current_session_name)
        if session_hours is not None:
            session_start = now.replace(hour=session_hours[0], minute=0, second=0, microsecond=0)
            _session_trades = [
                t for t in broker.closed_trades
                if t.get("closed_at") and datetime.fromisoformat(t["closed_at"]) >= session_start
            ]

        _task = asyncio.create_task(_loop())


async def stop() -> None:
    state.running = False
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
