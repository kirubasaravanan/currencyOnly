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

[ADD 2026-08-19, explicit user instruction] pineconnector_relay.py runs
alongside tradesgnl_relay.py as a SECOND, independent real connection from
the same paper trades -- not a replacement. Every call site below fires
both relays; each is independently no-op until its own license/webhook
env vars are configured, so leaving PineConnector unconfigured has zero
live effect on the existing TradeSgnl connection.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import config
from config import PAIRS, MAJORS, MTF_TIMEFRAMES, ENGINE_LOOP_SECONDS, SESSIONS_UTC, state
from data.market_data import market_data
from engine import risk, correlation, trade_manager, currency_strength, discord_alerts, tradesgnl_relay, pineconnector_relay, trade_sync_heartbeat, real_giveback_source, real_risk_source
from engine.analytics import NON_STRATEGY_EXIT_REASONS
from engine.entry import entry_signal, _in_session
from engine.macro_filter import calendar
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
    buckets them into the running session/day accumulators.

    [ADD 2026-08-21] reason == "real_sync_close" (trade_sync_heartbeat.py's
    direction-1 remediation: paper force-closed to match a REAL side
    already confirmed closed via a positively-matched MT5 deal) skips the
    two relay send_close() calls below -- sending a close command for a
    position that's already closed on that same real side is redundant at
    best. The Discord "trade closed" notice still fires either way."""
    global _alerted_closed_count
    new_trades = broker.closed_trades[_alerted_closed_count:]
    _alerted_closed_count = len(broker.closed_trades)
    for t in new_trades:
        await discord_alerts.alert_trade_closed(t)
        if t.get("reason") != "real_sync_close":
            await tradesgnl_relay.send_close(t, source="process_new_closed_trades")
            await pineconnector_relay.send_close(t, source="process_new_closed_trades")
        _session_trades.append(t)
        _day_trades.append(t)


# [ADD 2026-08-18, explicit user instruction, calibrated on a real 60-day
# backtest -- see config.DAILY_GIVEBACK_MIN_PEAK/_PCT for the evidence]
# [CHANGED 2026-08-20, explicit user instruction] Was paper-data-driven;
# now sources from the actual FundedNext MT5 account's real, realized P&L
# (engine/real_giveback_source.py) instead -- the breaker exists to protect
# real money, and this session repeatedly found paper's numbers can diverge
# from real by real dollars, so deciding off paper never made sense once a
# real account was in the picture. Threshold values (DAILY_GIVEBACK_MIN_PEAK/
# _PCT) are UNCHANGED, kept deliberately tight relative to a bigger
# FundedNext balance per explicit user instruction -- closing out early is
# the intent, to stay out of the riskiest NY-session volatility (6PM IST)
# rather than ride it.
#
# ACCOUNT-SCOPED, not global: per explicit user instruction ("tradsgnl
# account should not be blocked by the Daily giveback limit"), this now
# only ever touches PineConnector (the FundedNext-connected relay) -- both
# the forced closes and the entry-block below. TradeSgnl keeps trading
# completely unrestricted regardless of FundedNext's give-back status; it's
# a deliberately unconstrained data source, not a party to this account's
# risk limit. Paper is untouched either way, same as always -- it stays a
# clean, continuous research baseline.
#
# Fails safe: real_giveback_source returns None whenever the FundedNext
# account can't be verified reachable this cycle (terminal not running,
# wrong account, disconnected) -- treated as "no data, skip," never as
# "assume triggered" or "assume fine." Same "never fabricate" convention as
# trade_sync_heartbeat.py.
#
# [FIX 2026-08-18, found live, still applies] Persisting the triggered
# date to disk closes a restart-persistence gap: without it, a restart
# after the breaker already fired today could lose that state and silently
# re-permit PineConnector entries for the rest of a day that had already
# breached the limit.
_giveback_triggered_date: Optional[str] = None
_GIVEBACK_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "storage", "giveback_state.json")

# MT5 reads are blocking -- same reasoning as _check_sync_heartbeat's own
# cadence guard below, this must not run on every 60s scan.
GIVEBACK_CHECK_INTERVAL_SECONDS = 2 * 60
_last_giveback_check_at = 0.0

# [ADD 2026-08-21, explicit user instruction -- "Build 2"] Standalone
# aggregate-risk monitor, runs independent of any pending FX trade -- see
# real_risk_source._current_risk_sync's docstring for the gap this closes
# (the entry gate in _scan_once below can't catch the sister app's gold
# bridge opening on its own and pushing FundedNext's real combined risk
# over 3% with no FX trade pending to trigger a check). Same 2-minute
# cadence guard as the give-back breaker, same reasoning -- MT5 reads are
# blocking and shouldn't run on every 60s scan.
FUNDEDNEXT_RISK_CHECK_INTERVAL_SECONDS = 2 * 60
_last_fundednext_risk_check_at = 0.0
# Alerts only on the TRANSITION into breach (same pattern as
# _last_risk_block_reason below), and clears once risk drops back under
# 3% so a later, independent breach can alert fresh.
_fundednext_risk_breach_alerted = False


def _load_giveback_triggered_date() -> Optional[str]:
    try:
        with open(os.path.abspath(_GIVEBACK_STATE_FILE)) as f:
            return json.load(f).get("triggered_date")
    except Exception:  # noqa: BLE001
        return None


def _save_giveback_triggered_date(date_str: str) -> None:
    path = os.path.abspath(_GIVEBACK_STATE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"triggered_date": date_str}, f)



# [ADD 2026-08-21, explicit user instruction; scope narrowed same day]
# A manual counterpart to the give-back breaker above: "close everything
# and pause entries for today" triggered BY THE USER in the moment (e.g.
# a big move that won't last), not by an automatic P&L threshold.
# Deliberately a SEPARATE flag from _giveback_triggered_date, not a reuse
# of it -- kept independent in case their scopes ever need to diverge
# again, even though both are now PineConnector-only in practice.
#
# TradeSgnl is NEVER affected by this flag (nor by anything else in this
# file) -- explicit instruction: it runs on a demo account purely as a
# continuous data feed, so there's no real money for any stop mechanism
# to protect there. Gated only by the master real_relay_enabled switch.
# Same restart-persistence pattern as the give-back flag, same natural
# reset at IST midnight.
_manual_block_date: Optional[str] = None
_MANUAL_BLOCK_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "storage", "manual_block_state.json")


def _load_manual_block_date() -> Optional[str]:
    try:
        with open(os.path.abspath(_MANUAL_BLOCK_STATE_FILE)) as f:
            return json.load(f).get("blocked_date")
    except Exception:  # noqa: BLE001
        return None


def _save_manual_block_date(date_str: str) -> None:
    path = os.path.abspath(_MANUAL_BLOCK_STATE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"blocked_date": date_str}, f)


# [ADD 2026-08-21, explicit user instruction, prompted by a real incident
# the same day] A relay reporting "sent (200)" only means PineConnector's
# cloud service ACCEPTED the command -- it does NOT prove the EA actually
# executed it on the broker. Confirmed happening for real: a USDCHF entry
# logged a genuine MT5 deal on the FundedNext account, then vanished with
# zero exit deal anywhere in a 7-day window -- our own _confirmed_open_ids
# bookkeeping had no way to know. So close_all_real_positions() below
# doesn't just trust the relay's response; it re-reads FundedNext's actual
# positions afterward and reports anything still open as a genuine
# orphan, never assumed clear from the relay response alone.
#
# [ADD 2026-08-21, explicit user instruction] TradeSgnl is PineConnector-
# only no more -- it's now excluded from every stop/close mechanism in
# this file entirely, not just the automatic give-back breaker. TradeSgnl
# runs on a demo account purely as a continuous data feed; there's no real
# money to protect, so nothing -- not the give-back breaker, not
# !closeall/!close SYMBOL/!stopday, nothing -- should ever touch it. Its
# entries/exits are gated ONLY by the master real_relay_enabled switch
# (see the entry block in _scan_once() and tradesgnl_relay.py itself,
# which is otherwise untouched by anything in this module).
CLOSE_VERIFY_DELAY_SECONDS = 5  # let the EA actually process the close before re-reading


def _positions_still_open_sync(symbols: List[str]) -> Dict[str, List[str]]:
    """Synchronous -- call via asyncio.to_thread (MT5's API is blocking).
    Returns {"FundedNext": [...]} of symbols from `symbols` still
    genuinely open on that account -- TradeSgnl is deliberately not
    checked here at all, since nothing in this module ever closes a
    position there (see module comment above). Never launches a terminal
    that isn't already running, and never trusts a connection without
    verifying the login matches -- same conventions as
    trade_sync_heartbeat.py and real_giveback_source.py. A connection or
    read failure reports "UNVERIFIABLE (...)" rather than silently
    claiming "clear" -- an unconfirmed account must never be read as safe."""
    still_open: Dict[str, List[str]] = {"FundedNext": []}
    if not symbols:
        return still_open

    try:
        import MetaTrader5 as mt5
        import psutil
    except ImportError as exc:
        return {"FundedNext": [f"UNVERIFIABLE ({exc})"]}

    path = real_giveback_source.FUNDEDNEXT_MT5_TERMINAL_PATH
    login = real_giveback_source.FUNDEDNEXT_MT5_LOGIN
    if not login:
        return still_open  # not configured yet -- nothing to verify

    try:
        running = any(
            p.info["name"] == "terminal64.exe" and p.info["exe"] == path
            for p in psutil.process_iter(["name", "exe"])
        )
        if not running:
            still_open["FundedNext"] = ["UNVERIFIABLE (terminal not running)"]
            return still_open
        if not mt5.initialize(path=path):
            still_open["FundedNext"] = [f"UNVERIFIABLE (connect failed: {mt5.last_error()})"]
            return still_open
        acc = mt5.account_info()
        if acc is None or acc.login != login:
            still_open["FundedNext"] = ["UNVERIFIABLE (account mismatch)"]
            return still_open
        positions = mt5.positions_get() or ()
        open_symbols = {p.symbol for p in positions}
        still_open["FundedNext"] = [s for s in symbols if s in open_symbols]
    except Exception as exc:  # noqa: BLE001
        still_open["FundedNext"] = [f"UNVERIFIABLE ({exc})"]
    finally:
        mt5.shutdown()
    return still_open


async def close_all_real_positions(symbol: Optional[str] = None) -> Dict:
    """Closes open position(s) on PineConnector (FundedNext) ONLY, then
    verifies against the actual account rather than trusting the relay
    response alone (see comment above). TradeSgnl is never touched --
    it's a demo-account data feed, gated only by real_relay_enabled, not
    by anything in this function. Paper is untouched too -- it stays a
    clean, continuous baseline, same as the give-back breaker. Shared by
    discord_bot_listener.py's "!closeall"/"!close SYMBOL", the /close-all,
    /close-symbol/{symbol}, and /stop-day HTTP routes, and
    manual_stop_for_today() below, so there's exactly one close code
    path, not several.

    symbol: if given, closes only that one symbol's open position(s) and
    touches nothing else -- the system keeps running exactly as before
    for every other symbol, and even this same symbol can open a fresh
    trade again on the very next qualifying signal (no block flag is set
    here, unlike manual_stop_for_today()). If None (the default), closes
    everything, matching the prior behavior exactly.

    Returns {"closed_symbols": [...], "still_open": {"FundedNext": [...]}}
    -- a non-empty "still_open" list means a genuine orphan (or, prefixed
    "UNVERIFIABLE", that the check itself couldn't run) and needs a human
    to look, not an assumption that closing succeeded."""
    targets = [t for t in broker.open_positions if symbol is None or t["symbol"] == symbol]
    closed_symbols: List[str] = []
    for t in list(targets):
        sent_pineconnector = await pineconnector_relay.send_close(t, source="close_all_real_positions")
        if sent_pineconnector:
            closed_symbols.append(t["symbol"])

    if not closed_symbols:
        return {"closed_symbols": [], "still_open": {"FundedNext": []}}

    await asyncio.sleep(CLOSE_VERIFY_DELAY_SECONDS)
    still_open = await asyncio.to_thread(_positions_still_open_sync, closed_symbols)
    return {"closed_symbols": closed_symbols, "still_open": still_open}


async def manual_stop_for_today() -> Dict:
    """The user's manual "close everything and pause for today" command --
    PineConnector (FundedNext) only. TradeSgnl is never affected, see
    module comment above."""
    global _manual_block_date
    result = await close_all_real_positions()
    _manual_block_date = _current_ist_date
    _save_manual_block_date(_current_ist_date)
    return result


async def _check_daily_giveback_breaker() -> None:
    global _giveback_triggered_date, _last_giveback_check_at
    if _giveback_triggered_date == _current_ist_date:
        return

    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - _last_giveback_check_at < GIVEBACK_CHECK_INTERVAL_SECONDS:
        return
    _last_giveback_check_at = now_ts

    real_state = await real_giveback_source.get_today_real_giveback_state()
    if real_state is None:
        return  # not reachable this cycle -- no data, no action, try again next cycle

    peak = real_state["peak"]
    current = real_state["current"]

    if peak < config.DAILY_GIVEBACK_MIN_PEAK:
        return
    if current > peak * (1 - config.DAILY_GIVEBACK_PCT / 100.0):
        return

    _giveback_triggered_date = _current_ist_date
    _save_giveback_triggered_date(_current_ist_date)
    closed_symbols: List[str] = []
    for t in list(broker.open_positions):
        sent = await pineconnector_relay.send_close(t, source="giveback_breaker")
        if sent:
            closed_symbols.append(t["symbol"])
    await discord_alerts.alert_daily_giveback_triggered(peak, current, closed_symbols)


async def _check_fundednext_aggregate_risk() -> None:
    """[ADD 2026-08-21, explicit user instruction -- "Build 2", upgraded
    same day from alert-only to actually acting] Standing monitor,
    independent of any pending FX trade -- see
    real_risk_source._current_risk_sync's docstring and
    FUNDEDNEXT_RISK_CHECK_INTERVAL_SECONDS above for why this exists
    separately from the entry gate in _scan_once.

    This is a REAL compliance rule on a real funded challenge ("Max Risk:
    3% At any time"), not an opportunistic protection -- so on a detected
    breach this closes currencyOnly's own real PineConnector/FundedNext
    positions (the one lever this app actually has -- it cannot touch the
    sister app's gold bridge, which has no equivalent check of its own)
    via close_all_real_positions(), the same verified-not-trusted close
    path the give-back breaker uses. Paper and TradeSgnl are untouched,
    same as every other real-money mechanism in this file.

    If closing our own side isn't enough (gold alone is over 3%, or there
    was nothing of ours open to close), that's reported explicitly in the
    alert rather than silently -- this app has no further lever in that
    case; it needs direct attention on the account itself."""
    global _last_fundednext_risk_check_at, _fundednext_risk_breach_alerted

    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - _last_fundednext_risk_check_at < FUNDEDNEXT_RISK_CHECK_INTERVAL_SECONDS:
        return
    _last_fundednext_risk_check_at = now_ts

    snapshot = await real_risk_source.check_current_aggregate_risk()
    if snapshot is None:
        return  # not reachable this cycle -- no data, no action, try again next cycle

    if not snapshot["exceeds"]:
        _fundednext_risk_breach_alerted = False
        return

    if _fundednext_risk_breach_alerted:
        return  # already acted on this ongoing breach, don't repeat every cycle
    _fundednext_risk_breach_alerted = True

    close_result = await close_all_real_positions()
    closed_symbols = close_result.get("closed_symbols", [])

    after = await real_risk_source.check_current_aggregate_risk()
    after_line = (
        f"Real risk after closing our side: {after['current_open_risk_pct']:.2f}% "
        f"(${after['current_open_risk_usd']:.2f})."
        if after is not None
        else "Couldn't re-verify the resulting number right after closing -- check the account directly."
    )

    if closed_symbols:
        action_line = f"Closed currencyOnly's own real FundedNext position(s): {', '.join(closed_symbols)}."
    else:
        action_line = (
            "No currencyOnly FX positions were open on FundedNext to close -- this breach is "
            "coming entirely from something else (most likely the gold bridge). Nothing more "
            "this app can do from its side."
        )

    await discord_alerts.alert_engine_event(
        "🚨 FundedNext real open risk exceeded 3% -- closed our side",
        f"Before action: ${snapshot['current_open_risk_usd']:.2f} across "
        f"{snapshot['position_count']} open position(s) = {snapshot['current_open_risk_pct']:.2f}% "
        f"of ${snapshot['equity']:.2f} equity (limit {real_risk_source.MAX_RISK_PCT}%). "
        f"{action_line} {after_line} Paper and TradeSgnl untouched.",
    )


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
            relayed = await tradesgnl_relay.send_partial_close(t, source="process_partial_takes")
            await pineconnector_relay.send_partial_close(t, source="process_partial_takes")
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


# [ADD 2026-08-18, explicit user instruction] Tracks the currently-alerted
# risk-block reason so a breach only alerts on the TRANSITION into that
# state (not every 60s scan while it stays blocked), and clears once
# trading is allowed again so a future re-breach can alert fresh.
_last_risk_block_reason: Optional[str] = None


async def _check_risk_limit_alert(risk_check: Dict) -> None:
    global _last_risk_block_reason
    reason = risk_check.get("reason") if not risk_check["allowed"] else None
    if reason == _last_risk_block_reason:
        return
    _last_risk_block_reason = reason
    if reason is not None:
        await discord_alerts.alert_risk_limit_breached(reason, broker.equity, broker.peak_equity, _drawdown_pct())


# [ADD 2026-08-18, explicit user instruction: "a start of the day message
# kind of"] Daily status ping independent of whether anything is actually
# breached -- fires once, shortly before the earliest pair's session opens
# (05:30 IST), so risk-limit standing is visible proactively every day
# rather than only reactively when something trips.
SOD_TRIGGER_MINUTES = 5 * 60 + 25  # 05:25 IST
_last_sod_date: Optional[str] = None


async def _check_sod_status(now: datetime) -> None:
    global _last_sod_date
    date_str = _ist_date_str(now)
    minutes = _ist_minutes_of_day(now)
    if minutes >= SOD_TRIGGER_MINUTES and _last_sod_date != date_str:
        _last_sod_date = date_str
        await discord_alerts.alert_sod_status(date_str, broker.equity, broker.peak_equity, _drawdown_pct())


# [ADD 2026-08-25, explicit user instruction] Separate from SOD_TRIGGER_
# MINUTES above on purpose -- SOD fires at 05:25 IST, right as the
# earliest pairs' windows are just opening, before there's enough of the
# day's own trade history for a "how's today looking" check to say
# anything useful. 08:00 IST (per explicit user choice) gives a few hours
# of real activity to report on first.
MORNING_PULSE_TRIGGER_MINUTES = 8 * 60  # 08:00 IST
_last_morning_pulse_date: Optional[str] = None


async def _check_morning_pulse(now: datetime) -> None:
    global _last_morning_pulse_date
    date_str = _ist_date_str(now)
    minutes = _ist_minutes_of_day(now)
    if minutes < MORNING_PULSE_TRIGGER_MINUTES or _last_morning_pulse_date == date_str:
        return
    _last_morning_pulse_date = date_str

    clean_closed = [t for t in _day_trades if t.get("reason") not in NON_STRATEGY_EXIT_REASONS]
    contaminated_closed = [t for t in _day_trades if t.get("reason") in NON_STRATEGY_EXIT_REASONS]
    in_window = [s for s in PAIRS if _in_session(s, now)]
    out_of_window = [s for s in PAIRS if s not in in_window]
    events = calendar.events(24)

    await discord_alerts.alert_morning_pulse(
        date_str, broker.open_positions, clean_closed, contaminated_closed,
        in_window, out_of_window, events,
    )


_last_heartbeat_at = 0.0


async def _check_sync_heartbeat(prices: Optional[Dict[str, float]] = None) -> None:
    """Own cadence, separate from the 60s scan loop -- MT5 reads are
    blocking, so this runs in a thread and only every few minutes, not
    every scan. See trade_sync_heartbeat.py for what this actually checks
    (both TradeSgnl and FundedNext, since 2026-08-21) and why (explicit
    user request, 2026-08-17: "keep monitoring and let me know if anything
    else desyncs"; upgraded 2026-08-21 from alert-only to actually closing
    the confirmed-behind side to match).

    prices: this cycle's live price snapshot, threaded through so a
    direction-1 sync-close (paper force-closed to match a confirmed real
    exit) prices correctly on cross/JPY pairs -- see
    trade_sync_heartbeat.run_heartbeat_for's docstring."""
    global _last_heartbeat_at
    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - _last_heartbeat_at < trade_sync_heartbeat.HEARTBEAT_INTERVAL_SECONDS:
        return
    _last_heartbeat_at = now_ts
    result = await trade_sync_heartbeat.run_heartbeat(prices)
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
    await _check_daily_giveback_breaker()
    await _check_fundednext_aggregate_risk()
    await _check_session_rollover(now)
    await _check_eod_rollover(now)
    await _check_sod_status(now)
    await _check_morning_pulse(now)
    await _check_sync_heartbeat(prices)

    ranking = currency_strength.compute_ranking({s: f.get("1h") for s, f in frames_by_symbol.items()})

    # [FIX 2026-08-18, explicit user instruction -- found the same silent-
    # circuit-breaker gap the sister Forex app had, fixed there the day
    # before] can_open_new_trade() (max_open_trades/max_drawdown/daily_loss_
    # limit/weekly_loss_limit) was being called once per candidate symbol,
    # every scan, and its result silently discarded via `continue` -- no
    # log line, no Discord alert, nothing. Once tripped, new entries simply
    # stopped with zero visible explanation. Hoisted to a single account-
    # wide check per scan (the inputs never varied by symbol anyway) and
    # wired to alert_risk_limit_breached() on transition into a blocked
    # state (see _check_risk_limit_alert below).
    risk_check = risk.can_open_new_trade(broker.open_positions, broker.closed_trades, broker.equity, broker.peak_equity)
    await _check_risk_limit_alert(risk_check)

    open_symbols = {t["symbol"] for t in broker.open_positions}
    for symbol, frames in frames_by_symbol.items():
        if not risk_check["allowed"]:
            break
        if symbol in open_symbols or symbol not in prices:
            continue
        if trade_manager.in_cooldown(symbol, now, broker.closed_trades):
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
            # Paper always opens regardless of any gate below (per
            # explicit user instruction, it stays a clean, continuous
            # baseline) -- only the real relay sends are ever suppressed.
            # The two relays are gated INDEPENDENTLY, not together:
            #   - TradeSgnl: ONLY the manual real_relay_enabled switch.
            #     [ADD 2026-08-21, explicit user instruction] Never touched
            #     by ANYTHING else -- not the automatic give-back breaker,
            #     not !closeall/!close SYMBOL/!stopday, nothing. It's a
            #     demo account used purely as a continuous data feed, so
            #     none of the stop mechanisms built for protecting real
            #     money apply to it at all.
            #   - PineConnector (the FundedNext-connected relay): the same
            #     manual switch, PLUS the automatic give-back breaker
            #     (protects this specific real account), PLUS
            #     _manual_block_date (the user's own "!stopday" command),
            #     PLUS config.PINECONNECTOR_EXCLUDED_PAIRS -- new trial
            #     pairs (2026-08-21: EURJPY/NZDJPY/CADJPY/EURAUD/EURNZD)
            #     relay to TradeSgnl normally (demo, no real money) but
            #     stay off the real FundedNext account until their
            #     TradeSgnl monitoring period is satisfactory. PLUS
            #     [ADD 2026-08-21] a real-time check against FundedNext's
            #     actual "Max Risk: 3% At any time" rule -- see
            #     real_risk_source.py's docstring for why this checks the
            #     REAL account directly rather than trusting that paper's
            #     smaller equity keeps the relayed lot sizes safely under
            #     3% by coincidence, and why "can't verify" fails CLOSED
            #     here specifically (a real compliance rule, not an
            #     opportunistic protection like the give-back breaker).
            if config.state.real_relay_enabled:
                await tradesgnl_relay.send_entry(trade)
                if (
                    _giveback_triggered_date != _current_ist_date
                    and _manual_block_date != _current_ist_date
                    and symbol not in config.PINECONNECTOR_EXCLUDED_PAIRS
                ):
                    is_long = trade["side"] == "BULLISH"
                    risk_check = await real_risk_source.check_pineconnector_risk_ok(
                        symbol, trade["lots"], trade["entry_price"], trade["sl_price"], is_long
                    )
                    if risk_check is not None and not risk_check["would_exceed"]:
                        print(
                            f"[real_risk_source] {symbol}: projected open risk "
                            f"{risk_check['projected_open_risk_pct']:.3f}% of ${risk_check['equity']:.2f} "
                            f"equity (limit {real_risk_source.MAX_RISK_PCT}%) -- OK, sending to PineConnector"
                        )
                        await pineconnector_relay.send_entry(trade)
                    elif risk_check is not None and risk_check["would_exceed"]:
                        await discord_alerts.alert_engine_event(
                            "⚠️ PineConnector entry skipped -- would exceed FundedNext's 3% max-risk rule",
                            f"{symbol}: current open risk ${risk_check['current_open_risk_usd']:.2f} + this "
                            f"trade ${risk_check['new_trade_risk_usd']:.2f} = "
                            f"{risk_check['projected_open_risk_pct']:.2f}% of ${risk_check['equity']:.2f} equity "
                            f"(limit {real_risk_source.MAX_RISK_PCT}%). TradeSgnl and paper still opened normally.",
                        )
                    # risk_check is None (unreachable) -- fail closed, silently skip
                    # this cycle. No alert spam for a routine "MT5 not up" case; the
                    # existing heartbeat/giveback checks already surface real
                    # connectivity problems with this account.
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
        # Re-arm each relay's confirmed-sent tracking for whatever's already
        # open in the paper broker -- otherwise a restart silently strands
        # those positions' closes/partials (see tradesgnl_relay.seed_confirmed_ids).
        # Both relays seed independently -- pineconnector_relay staying
        # unconfigured means its own seed call is simply inert (no
        # PINECONNECTOR_LICENSE_ID means every one of its own functions
        # no-ops regardless of what's seeded).
        tradesgnl_relay.seed_confirmed_ids(broker.open_positions)
        pineconnector_relay.seed_confirmed_ids(broker.open_positions)
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

        # [FIX 2026-08-18, pre-empting the same restart-persistence bug
        # found three times already today] Both new trackers below are
        # plain in-memory state, same as everything fixed above -- without
        # seeding them, a same-day restart would either replay a duplicate
        # start-of-day status message (if already past 05:25 IST) or
        # re-fire a risk-limit-breach alert for a problem that was already
        # reported before the restart.
        global _last_sod_date, _last_risk_block_reason, _last_eod_date, _giveback_triggered_date, _manual_block_date, _last_morning_pulse_date
        if _ist_minutes_of_day(now) >= SOD_TRIGGER_MINUTES:
            _last_sod_date = _ist_date_str(now)
        if _ist_minutes_of_day(now) >= MORNING_PULSE_TRIGGER_MINUTES:
            _last_morning_pulse_date = _ist_date_str(now)
        # [FIX 2026-08-18, found live -- user reported the EOD summary firing
        # again after a restart] Unlike _last_sod_date just above, this had
        # no restart seeding at all -- any restart after 20:05 IST left
        # _last_eod_date as None, so the next scan's _check_eod_rollover saw
        # "not sent today" and re-fired the EOD Discord summary, duplicating
        # it. Same fix as SOD: if we're already past the trigger time for
        # today, treat today as already covered.
        if _ist_minutes_of_day(now) >= EOD_TRIGGER_MINUTES:
            _last_eod_date = _ist_date_str(now)
        startup_risk_check = risk.can_open_new_trade(broker.open_positions, broker.closed_trades, broker.equity, broker.peak_equity)
        _last_risk_block_reason = startup_risk_check.get("reason") if not startup_risk_check["allowed"] else None

        _giveback_triggered_date = _load_giveback_triggered_date()
        _manual_block_date = _load_manual_block_date()

        _task = asyncio.create_task(_loop())


async def stop() -> None:
    state.running = False
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
