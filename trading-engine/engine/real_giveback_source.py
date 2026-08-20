"""Reads TODAY's real, realized P&L directly from the FundedNext MT5
account, for the daily give-back breaker to trigger against -- replacing
the paper-data-driven version. [ADD 2026-08-20, explicit user instruction]

Why: the give-back breaker exists to protect REAL money, but until now it
decided based on PAPER's numbers, which this session has repeatedly shown
can diverge from real by real dollars (fill-price/timing differences,
sometimes real ahead, sometimes behind). Sourcing the trigger from the
actual account closes that gap -- and picks up real commission for free,
where paper only ever had a modeled placeholder.

Account is shared with the sister Forex app's gold bridge (PineConnector,
comment prefix "pineconnector_gold-"), so this must only count OUR OWN
trades -- filtered by comment tag, not by excluding XAUUSD specifically.
That's a deliberate choice, not an oversight: tag-based inclusion also
protects against anything else unexpected ever landing on this shared
account, not just gold. Verified the tag scheme is self-disjoint: our own
tag is "pineconnector-{symbol}-{L|S}" (pineconnector_relay.py's
_comment_id) -- "pineconnector_gold-XAUUSD-L".startswith("pineconnector-")
is False (14th char is "_" vs "-"), so the gold bridge's trades are
excluded by construction, no separate symbol check needed (a redundant
XAUUSD guard is still kept below as defense-in-depth).

[IMPORTANT, unverified] MT5_SERVER_UTC_OFFSET below is COPIED from
trade_sync_heartbeat.py's own empirically-confirmed value for the
DIFFERENT account/broker that module reads (110875560, Third Mt5) -- it is
NOT yet confirmed for whatever broker FundedNext actually runs on. Treat
this as a placeholder needing its own verification (compare a real
FundedNext trade's MT5 deal .time against its known true UTC open time,
same method used to confirm the original value) before trusting this
module's day-boundary math on day one.

Exit deals' own comment field gets overwritten by the broker (e.g.
"[tp 1.123]"), same finding trade_sync_heartbeat.py already made -- so
"ours" is determined by matching each closing deal's position_id back to
an OPENING deal (entry=0) that carries our comment tag, not by reading the
closing deal's own comment.

Read-only. Never closes, modifies, or places anything -- this module only
ever answers "what happened today," the orchestrator decides what to do
with that answer.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()

# [ADD 2026-08-20] Terminal path defaults to the Secondary MT5 install --
# per explicit user plan, FundedNext's credentials will replace the
# PineConnector demo (10011965423) already logged into this SAME terminal,
# not a new install. FUNDEDNEXT_MT5_LOGIN stays unset (0) until the user
# actually provides it -- every function below no-ops/returns None while
# it's 0, same convention as every other relay in this repo.
FUNDEDNEXT_MT5_TERMINAL_PATH = os.getenv(
    "FUNDEDNEXT_MT5_TERMINAL_PATH", r"C:\Users\Administrator\Pictures\Secondary MT5\terminal64.exe"
)
FUNDEDNEXT_MT5_LOGIN = int(os.getenv("FUNDEDNEXT_MT5_LOGIN", "0"))

OUR_COMMENT_PREFIX = "pineconnector-"

# See module docstring -- copied from trade_sync_heartbeat.py's confirmed
# value for a DIFFERENT account, needs its own verification against a real
# FundedNext deal before day one.
MT5_SERVER_UTC_OFFSET = timedelta(hours=3)

IST_OFFSET = timedelta(hours=5, minutes=30)


def _terminal_running() -> bool:
    """Never auto-launches the terminal -- only ever piggybacks on one
    already running for real trading, same principle as every other
    direct-MT5-read module in this codebase."""
    import psutil
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            if proc.info["name"] == "terminal64.exe" and proc.info["exe"] == FUNDEDNEXT_MT5_TERMINAL_PATH:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _connect(mt5mod) -> bool:
    if not mt5mod.initialize(path=FUNDEDNEXT_MT5_TERMINAL_PATH):
        return False
    acc = mt5mod.account_info()
    return acc is not None and acc.login == FUNDEDNEXT_MT5_LOGIN


def _to_true_utc(server_ts: float) -> datetime:
    return datetime.fromtimestamp(server_ts, tz=timezone.utc) - MT5_SERVER_UTC_OFFSET


def _ist_date_str(dt: datetime) -> str:
    return (dt + IST_OFFSET).date().isoformat()


def _get_today_real_pnl_sync() -> Optional[Dict]:
    """Synchronous -- call via asyncio.to_thread. Returns None (never a
    fabricated number) whenever the account can't be verified reachable
    right now -- caller must treat None as 'no data this cycle, skip'."""
    if not FUNDEDNEXT_MT5_LOGIN:
        return None
    if not _terminal_running():
        return None

    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    if not _connect(mt5):
        return None

    try:
        now_utc = datetime.now(timezone.utc)
        today = _ist_date_str(now_utc)
        # Wide window, not a tight one -- history_deals_get() by date-range
        # has repeatedly, silently returned incomplete/empty results this
        # session for narrower windows even when deals genuinely existed;
        # widening past what's needed and filtering client-side is the
        # reliable pattern found in practice.
        frm_server = now_utc - timedelta(days=2) + MT5_SERVER_UTC_OFFSET
        to_server = now_utc + timedelta(days=1) + MT5_SERVER_UTC_OFFSET
        deals = mt5.history_deals_get(frm_server, to_server)
        if deals is None:
            return None

        our_position_ids = {
            d.position_id for d in deals
            if getattr(d, "entry", None) == 0 and str(d.comment).startswith(OUR_COMMENT_PREFIX)
        }

        todays_exit_deals = []
        for d in deals:
            if getattr(d, "entry", None) != 1:
                continue
            if d.position_id not in our_position_ids:
                continue
            if d.symbol == "XAUUSD":  # defense-in-depth, see module docstring
                continue
            true_utc = _to_true_utc(d.time)
            if _ist_date_str(true_utc) != today:
                continue
            todays_exit_deals.append(d)

        todays_exit_deals.sort(key=lambda d: d.time)

        running = 0.0
        peak = 0.0
        for d in todays_exit_deals:
            running += d.profit + d.commission + d.swap
            peak = max(peak, running)

        return {
            "reachable": True,
            "peak": round(peak, 2),
            "current": round(running, 2),
            "deal_count": len(todays_exit_deals),
            "account_login": FUNDEDNEXT_MT5_LOGIN,
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        mt5.shutdown()


async def get_today_real_giveback_state() -> Optional[Dict]:
    """Async wrapper -- MT5's Python API is blocking, must never run
    directly on the main scan loop."""
    import asyncio
    return await asyncio.to_thread(_get_today_real_pnl_sync)
