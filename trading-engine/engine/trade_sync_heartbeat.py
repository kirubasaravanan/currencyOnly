"""Trade sync heartbeat — bidirectional reconciliation between the paper
broker's trade ledger and the real MT5 account (license 3116556667670,
login 110875560) that tradesgnl_relay.py mirrors trades to.

[ADDED 2026-08-17, explicit user instruction: "keep monitoring and let me
know if anything else desyncs"] Prompted by a real, caught-live incident
the same day: restarting the engine to deploy an unrelated Discord-alert
change reset tradesgnl_relay's in-process _confirmed_open_ids tracking set,
which silently no-op'd EURUSD's close command — the real MT5 position sat
open for 16+ minutes after paper had already exited it, with zero error
logged anywhere. That specific bug is fixed (orchestrator.start() now
reseeds those sets from broker.open_positions), but this module exists as
an ongoing safety net for the same *class* of problem — any future bug,
webhook drop, or broker-side rejection that leaves the two ledgers out of
sync — rather than trusting that one fix covers every way it could recur.

Ported from the sister Forex app's engine/trade_sync_heartbeat.py +
mt5_reconcile.py (read directly via SSH, same source tradesgnl_relay.py's
command grammar was ported from), simplified for this app's single-account,
webhook-only setup: no mt5_ipc_lock (only one MT5 terminal is ever in play
here, unlike the sister app's two), no mt5_direct (this app never calls
mt5.order_send() — see tradesgnl_relay.py's own docstring), no
EXCLUDED_SYMBOLS (every one of the 17 pairs is relay-eligible here).

Two directions, both alert-only — this module NEVER force-closes anything,
on either side. Direction 1 (paper open, real not open) only claims a
confirmed phantom when a real closing deal is positively found in MT5's
own history; absence from positions_get() alone is treated as "uncertain,
keep watching," never acted on, matching the sister app's own hard lesson
(its 2026-07-29 incident: conflating "missing from a live read" with
"confirmed closed" force-closed 28 genuinely-open positions at a
fabricated $0.00 during a connection outage). Direction 2 (paper closed,
real still open) is exactly the EURUSD case above.

Grace period: any reconnect failure resets a "distrust findings" window,
so a flapping MT5 connection doesn't get read as a wave of real problems.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from engine.paper_broker import broker
from engine.tradesgnl_relay import _comment_id, TRADESGNL_LICENSE_ID

MT5_TERMINAL_PATH = r"C:\Users\Administrator\Pictures\Third Mt5\terminal64.exe"
MT5_ACCOUNT_LOGIN = 110875560

# This demo account's server clock runs 3h ahead of true UTC (empirically
# confirmed 2026-08-17 by comparing paper trades' own opened_at UTC
# timestamps against MT5 positions_get()'s .time for the same trades —
# GBPAUD/NZDUSD/EURGBP all matched +3:00:0x exactly). MT5's own
# positions/history timestamps are all in this server time, not true UTC.
MT5_SERVER_UTC_OFFSET = timedelta(hours=3)

HEARTBEAT_ENABLED = True
HEARTBEAT_INTERVAL_SECONDS = 5 * 60
GRACE_PERIOD_SECONDS = 10 * 60
DIRECTION2_LOOKBACK_HOURS = 24
DIRECTION2_REALERT_SECONDS = 60 * 60

_last_unreachable_at: Optional[float] = None
_direction2_last_alerted: Dict[int, float] = {}  # real_ticket -> ts


def _in_grace_period() -> bool:
    return _last_unreachable_at is not None and (time.time() - _last_unreachable_at) < GRACE_PERIOD_SECONDS


def _mark_unreachable() -> None:
    global _last_unreachable_at
    _last_unreachable_at = time.time()


def _terminal_running() -> bool:
    """True only if the terminal is already running on its own -- mt5.initialize()
    will silently LAUNCH it otherwise, which this heartbeat must never do
    (it should only ever piggyback on a terminal already open for real trading)."""
    import psutil
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            if proc.info["name"] == "terminal64.exe" and proc.info["exe"] == MT5_TERMINAL_PATH:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _connect(mt5mod) -> bool:
    if not mt5mod.initialize(path=MT5_TERMINAL_PATH):
        return False
    acc = mt5mod.account_info()
    return acc is not None and acc.login == MT5_ACCOUNT_LOGIN


def _to_true_utc(server_ts: float) -> datetime:
    return datetime.fromtimestamp(server_ts, tz=timezone.utc) - MT5_SERVER_UTC_OFFSET


def _to_server_utc(true_utc: datetime) -> datetime:
    return true_utc + MT5_SERVER_UTC_OFFSET


def _find_closing_deal(mt5mod, symbol: str, tag: str, opened_at_true_utc: datetime):
    """The real MT5 deal that closed this position, found via MT5's own
    position_id linkage between the opening deal (entry=0, tagged with our
    comment) and its closing deal (entry=1 -- closing legs never carry our
    comment, the broker overwrites it, e.g. "[tp 1.16249]"). Returns None
    if no matching opening deal is found (can't safely guess which closing
    deal belongs to it)."""
    now = datetime.now(timezone.utc)
    frm_server = _to_server_utc(opened_at_true_utc - timedelta(minutes=10))
    to_server = _to_server_utc(now)
    deals = mt5mod.history_deals_get(frm_server, to_server, group=f"*{symbol}*")
    if not deals:
        return None
    deals = sorted(deals, key=lambda d: d.time)
    opens = [d for d in deals if getattr(d, "entry", None) == 0 and d.comment == tag]
    if not opens:
        return None
    open_deal = min(opens, key=lambda d: abs(_to_true_utc(d.time).timestamp() - opened_at_true_utc.timestamp()))
    closes = [d for d in deals if getattr(d, "entry", None) == 1 and d.position_id == open_deal.position_id]
    if not closes:
        return None
    return closes[-1]


def _check() -> Dict:
    """Never raises -- a failure here must not take down the scan loop."""
    result: Dict = {
        "reachable": False,
        "direction1_confirmed_phantoms": [],
        "direction1_uncertain": [],
        "direction2_still_open_on_real": [],
        "errors": [],
    }
    if not TRADESGNL_LICENSE_ID:
        result["errors"].append("TRADESGNL_LICENSE_ID not configured -- nothing to reconcile against")
        return result
    if not _terminal_running():
        _mark_unreachable()
        result["errors"].append("MT5 terminal not running -- skipped (never auto-launches it)")
        return result

    try:
        import MetaTrader5 as mt5
    except ImportError:
        result["errors"].append("MetaTrader5 package not installed")
        return result

    if not _connect(mt5):
        _mark_unreachable()
        result["errors"].append(f"connect/verify failed: {mt5.last_error()}")
        return result

    result["reachable"] = True
    in_grace = _in_grace_period()

    try:
        real_positions = mt5.positions_get() or ()
        real_open_tags = {(p.symbol, p.comment) for p in real_positions}
        real_open_by_tag = {(p.symbol, p.comment): p for p in real_positions}
        internal_open_tags = {
            (t.get("symbol"), _comment_id(t.get("symbol"), t.get("side", "")))
            for t in broker.open_positions
        }

        # --- Direction 1: paper open, real not open ---
        for t in list(broker.open_positions):
            symbol = t.get("symbol")
            side = t.get("side", "")
            tag = _comment_id(symbol, side)
            if (symbol, tag) in real_open_tags:
                continue  # genuinely open on both sides

            opened_at_str = t.get("opened_at")
            try:
                opened_at = datetime.fromisoformat(opened_at_str) if opened_at_str else datetime.now(timezone.utc)
            except ValueError:
                opened_at = datetime.now(timezone.utc)
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)

            deal = _find_closing_deal(mt5, symbol, tag, opened_at)
            if deal is None or in_grace:
                result["direction1_uncertain"].append({
                    "symbol": symbol, "side": side,
                    "internal_pnl": t.get("pnl"),
                    "reason": "in_grace_period" if in_grace else "no_matching_closing_deal_found",
                })
                continue

            result["direction1_confirmed_phantoms"].append({
                "trade_id": t["id"], "symbol": symbol, "side": side,
                "internal_pnl": t.get("pnl"),
                "real_exit_price": deal.price, "real_pnl": round(deal.profit, 2),
                "real_close_time_utc": _to_true_utc(deal.time).isoformat(),
            })

        # --- Direction 2: paper closed (recently), real still open ---
        if not in_grace:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=DIRECTION2_LOOKBACK_HOURS)
            for t in broker.closed_trades:
                symbol = t.get("symbol")
                closed_at_str = t.get("closed_at")
                if not closed_at_str:
                    continue
                try:
                    closed_at = datetime.fromisoformat(closed_at_str)
                except ValueError:
                    continue
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=timezone.utc)
                if closed_at < cutoff:
                    continue
                tag = _comment_id(symbol, t.get("side", ""))
                if (symbol, tag) in internal_open_tags:
                    # A newer paper position with the same symbol+side is
                    # open right now -- the real position matches THAT one,
                    # not this older closed trade. Direction 1 above already
                    # covers whether the newer one is in sync.
                    continue
                real_pos = real_open_by_tag.get((symbol, tag))
                if real_pos is None:
                    continue  # genuinely closed on both sides -- good
                last_alerted = _direction2_last_alerted.get(real_pos.ticket, 0.0)
                if time.time() - last_alerted < DIRECTION2_REALERT_SECONDS:
                    continue
                _direction2_last_alerted[real_pos.ticket] = time.time()
                result["direction2_still_open_on_real"].append({
                    "symbol": symbol, "side": t.get("side"),
                    "paper_closed_at": closed_at_str, "paper_close_reason": t.get("reason"),
                    "real_ticket": real_pos.ticket, "real_profit": round(real_pos.profit, 2),
                    "real_volume": real_pos.volume,
                })
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"heartbeat check error: {exc}")
    finally:
        mt5.shutdown()

    return result


def run_heartbeat() -> Dict:
    """Synchronous -- call via asyncio.to_thread (MT5's Python API is
    blocking). Alert-only: never closes anything on either side. Returns
    the check result so the caller can decide whether it's worth alerting."""
    if not HEARTBEAT_ENABLED:
        return {"disabled": True}
    return _check()
