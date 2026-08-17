"""TradeSgnl webhook relay — REAL order execution to an MT5 account.

Ported from the proven, already-live implementation in the sister Forex
app's engine/tradesgnl_relay.py (command format verified directly against
that working integration, not guessed — read over SSH before writing this).
Fires only from the live orchestrator, mirroring live paper trades one-for-
one; never touched by the backtester, so a historical replay can never
send a real command.

[ADD 2026-08-16, explicit user instruction] License 3116556667670, demo
MT5 account (login 110875560, MetaQuotes-Demo server, ~$15k). Verified
BEFORE wiring this up, not assumed: read the Forex app's
gold_fx3_bridge.py directly and confirmed its `entry_enabled=False`
hardcoded flag (set 2026-08-12, that app's own prior explicit instruction)
makes its send_entry() return immediately every time — so the Forex app
cannot place new entries on this account regardless of any other setting —
and separately pulled the account's actual MT5 trade history (via the
MetaTrader5 Python package against the specific "Third Mt5" terminal
under the Administrator profile on the VPS) rather than trusting either
app's config alone: 2 trades total, both closed, comment tags confirming
their real sources (one from gold_fx3_bridge, one from a separate
TradingView-sourced strategy). This module is a SEPARATE, additional
relay to that same demo account/license — not a copy of anything the
Forex app already owns.

No-ops automatically (does nothing, logs nothing sent) whenever
TRADESGNL_LICENSE_ID is unset in .env — same convention the sister app
uses for every one of its own relay targets. This is a real behavior
change to this repo's standing "zero order-placement code" guarantee —
see README.md for the explicit, current statement of what this does and
how to turn it off.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Dict, Set

import requests
from dotenv import load_dotenv

from config import PIP_SIZE

load_dotenv()

TRADESGNL_LICENSE_ID = os.getenv("TRADESGNL_LICENSE_ID", "")
TRADESGNL_WEBHOOK_URL = os.getenv("TRADESGNL_WEBHOOK_URL", "https://webhook.tradesgnl.com")

_last_send_ts = 0.0
_MIN_INTERVAL_SECONDS = 1.0

# Only send_close() for a trade ID we actually confirmed send_entry() sent
# successfully for — mirrors the sister app's own fix for the same bug
# (close commands firing for tickets that were never really opened on the
# relayed account because the entry send failed or was skipped).
_confirmed_open_ids: Set[int] = set()


def _fmt_price(symbol: str, price: float) -> str:
    pip = PIP_SIZE.get(symbol, 0.0001)
    decimals = 3 if pip >= 0.01 else 5
    return f"{price:.{decimals}f}"


def _comment_id(symbol: str, side: str) -> str:
    is_long = side in ("BUY", "BULLISH")
    return f"currencyonly-{symbol}-{'L' if is_long else 'S'}"


def _send_sync(command: str) -> bool:
    global _last_send_ts
    if not TRADESGNL_LICENSE_ID:
        return False  # not configured -- no-op, matching the sister app's own convention
    elapsed = time.time() - _last_send_ts
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    try:
        r = requests.post(
            TRADESGNL_WEBHOOK_URL, data=command,
            headers={"Content-Type": "text/plain"}, timeout=10,
        )
        _last_send_ts = time.time()
        if r.status_code in (200, 201, 202, 204):
            print(f"[tradesgnl_relay] SENT ({r.status_code}): {command} | response: {r.text[:200]}")
            return True
        print(f"[tradesgnl_relay] webhook returned {r.status_code}: {r.text[:200]} | command was: {command}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[tradesgnl_relay] webhook error: {exc}")
        return False


async def send_entry(trade: Dict) -> None:
    if not TRADESGNL_LICENSE_ID:
        return
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    action = "buy" if is_long else "sell"
    command = (
        f"{TRADESGNL_LICENSE_ID},{symbol},{action},"
        f"risk={trade['lots']:.2f},"
        f"sl_price={_fmt_price(symbol, trade['sl_price'])},"
        f"tp_price={_fmt_price(symbol, trade['tp_price'])},"
        f"comment={_comment_id(symbol, trade['side'])}"
    )
    sent = await asyncio.to_thread(_send_sync, command)
    if sent:
        _confirmed_open_ids.add(trade["id"])


async def send_close(trade: Dict) -> None:
    if not TRADESGNL_LICENSE_ID:
        return
    if trade["id"] not in _confirmed_open_ids:
        return  # entry was never confirmed-sent -- nothing real to close
    _confirmed_open_ids.discard(trade["id"])
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    close_action = "closelong" if is_long else "closeshort"
    command = f"{TRADESGNL_LICENSE_ID},{symbol},{close_action},comment={_comment_id(symbol, trade['side'])}"
    await asyncio.to_thread(_send_sync, command)
