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

[FIX 2026-08-16, same day, explicit user instruction] First live fills
(NZDCAD, AUDCAD) showed the real MT5 position closing via the broker's
own static pending TP order at the ORIGINAL entry-time price, days before
the paper engine's own trailing-stop logic would have exited — a real
mismatch, not a timing artifact. Root cause: TradeSgnl does not support
modifying an already-open order's SL/TP via webhook (documented directly
in the user's own V109 Pine script's header comment: "the broker-side
order keeps whatever SL/TP was sent at entry until Pine itself fires a
closelong/closeshort command"). Sending the paper engine's real tp_price
at entry means the broker's own pending order can fire on its own,
completely bypassing the paper engine's actual exit logic (50%
partial-at-TP1, then a trailing stop on the remainder that usually exits
well before the fixed TP2 — see paper_broker.py). Fixed two ways:
  1. Entry now sends a deliberately unreachable TP (WIDE_TP_MULTIPLIER x
     the real target distance) instead of the real one -- the broker-side
     pending order effectively never fires on its own; the paper engine's
     own explicit close command (send_close, or send_partial_close below)
     becomes the ONLY thing that ever closes or trims the real position.
     SL stays real and tight -- it's the correct safety net if this
     engine ever crashes or loses connectivity, which a wide TP is not.
  2. Added send_partial_close(), using the "closelongvol"/"closeshortvol"
     command grammar documented in the user's own V109 script's own
     partial-close alert code -- relays the 50%-at-TP1 partial exactly
     when paper_broker.py's own _take_partial() fires (see orchestrator.py
     for the detection hook, since _take_partial() doesn't move a trade
     into closed_trades the way a full close does).
  Note: this does NOT retroactively fix positions already opened with the
  old tight TP before this change -- those still carry their original
  static broker-side TP until they close on their own.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Dict, Set

import requests
from dotenv import load_dotenv

import config
from config import PIP_SIZE

load_dotenv()

TRADESGNL_LICENSE_ID = os.getenv("TRADESGNL_LICENSE_ID", "")
TRADESGNL_WEBHOOK_URL = os.getenv("TRADESGNL_WEBHOOK_URL", "https://webhook.tradesgnl.com")

_last_send_ts = 0.0
_MIN_INTERVAL_SECONDS = 1.0

# How far past the real target the broker-side pending TP is pushed, in
# multiples of the real entry-to-TP2 distance -- large enough that it
# should never be reached before the paper engine's own trailing-stop
# logic decides to exit and sends an explicit close/partial-close itself.
WIDE_TP_MULTIPLIER = 20.0

# Only send_close() for a trade ID we actually confirmed send_entry() sent
# successfully for — mirrors the sister app's own fix for the same bug
# (close commands firing for tickets that were never really opened on the
# relayed account because the entry send failed or was skipped).
_confirmed_open_ids: Set[int] = set()

# Track which trade IDs already had their 50%-partial relayed, so a
# second scan seeing the same already-partial-taken trade doesn't
# double-send the partial-close command.
_relayed_partial_ids: Set[int] = set()


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


def _wide_tp_price(trade: Dict) -> float:
    """Deliberately unreachable TP -- see the module docstring's 2026-08-16
    fix note. Extends WIDE_TP_MULTIPLIER x the real entry-to-TP2 distance
    past entry, in the trade's own direction (tp2_price - entry_price is
    already signed correctly for both long and short)."""
    entry = trade["entry_price"]
    return entry + WIDE_TP_MULTIPLIER * (trade["tp2_price"] - entry)


def seed_confirmed_ids(open_trades: list) -> None:
    """[FIX 2026-08-17, discovered live] _confirmed_open_ids/_relayed_partial_ids
    are plain in-process sets with no persistence -- a process restart (e.g.
    to deploy an unrelated code change) reset them to empty, which silently
    broke send_close()/send_partial_close() for every trade that was already
    open at restart time: their close would hit `if trade["id"] not in
    _confirmed_open_ids: return` and no-op with zero log output, permanently
    stranding that position open on the real MT5 account even after the
    paper trade closed. First caught in production (EURUSD close silently
    dropped after a restart deployed the exit-alert entry-time change).
    Call once at orchestrator startup with the paper broker's current
    open_positions -- anything already open is assumed to have gone through
    send_entry() at some point, so its close/partial should be allowed to
    relay again."""
    for t in open_trades:
        _confirmed_open_ids.add(t["id"])
        if t.get("partial_taken"):
            _relayed_partial_ids.add(t["id"])


async def send_entry(trade: Dict) -> None:
    if not TRADESGNL_LICENSE_ID:
        return
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    action = "buy" if is_long else "sell"
    # config.EXIT_MODE drives both paper_broker.py and this relay
    # identically -- "static" sends the REAL tp_price and lets the
    # broker's own pending order do the work (no partial, no trailing on
    # the paper side either); "dynamic" sends a deliberately unreachable
    # TP and relies entirely on explicit close/partial-close commands,
    # matching paper's own partial+trailing behavior.
    tp_to_send = trade["tp_price"] if config.state.exit_mode == "static" else _wide_tp_price(trade)
    command = (
        f"{TRADESGNL_LICENSE_ID},{symbol},{action},"
        f"risk={trade['lots']:.2f},"
        f"sl_price={_fmt_price(symbol, trade['sl_price'])},"
        f"tp_price={_fmt_price(symbol, tp_to_send)},"
        f"comment={_comment_id(symbol, trade['side'])}"
    )
    sent = await asyncio.to_thread(_send_sync, command)
    if sent:
        _confirmed_open_ids.add(trade["id"])
    else:
        from engine import discord_alerts
        await discord_alerts.alert_relay_failure(symbol, "entry", command)


async def send_partial_close(trade: Dict, source: str = "unknown") -> bool:
    """Relays the 50%-at-TP1 partial the paper engine just took (see
    paper_broker.py's _take_partial()). Only fires for a trade we confirmed
    the entry for, and only once per trade. Returns whether it actually
    sent successfully -- orchestrator.py surfaces this directly to Discord
    (alert_partial_close) since partial-close relay reliability is
    something the user explicitly asked to keep watching after the
    2026-08-17 GBPJPY incident (see below).

    [FIX 2026-08-17, found live] Previously sent a made-up "closelongvol"/
    "closeshortvol,vol_lots=X" command -- TradeSgnl rejected it outright
    (HTTP 400, error 7002 "Invalid action/command") for GBPJPY, leaving the
    real position stuck at full size while paper had already scaled to
    half. That grammar was never real; it was inferred from Pine alert-
    message wording, not verified. The actual, TradeSgnl-documented partial
    close reuses the SAME closelong/closeshort verb as a full close, with a
    pct= parameter -- straight from V109-Currency-Fixed.pine line 1311 (
    "TradeSgnl's documented parameter is pct=, not pct_percent="), just
    never exercised there since that script pins is_xau false."""
    if not TRADESGNL_LICENSE_ID:
        return False
    if trade["id"] not in _confirmed_open_ids:
        print(f"[tradesgnl_relay] SKIPPED partial-close for trade {trade['id']} ({trade['symbol']}) -- "
              f"id not in _confirmed_open_ids (see seed_confirmed_ids' 2026-08-17 GBPJPY incident note); "
              f"real position may now be desynced from paper")
        return False
    if trade["id"] in _relayed_partial_ids:
        return False
    _relayed_partial_ids.add(trade["id"])
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    close_action = "closelong" if is_long else "closeshort"
    command = (
        f"{TRADESGNL_LICENSE_ID},{symbol},{close_action},"
        f"pct=0.5,"
        f"comment={_comment_id(symbol, trade['side'])}"
    )
    sent = await asyncio.to_thread(_send_sync, command)
    print(f"[tradesgnl_relay] partial-close source={source} trade={trade['id']} ({symbol}) sent={sent}")
    return sent


async def send_close(trade: Dict, source: str = "unknown") -> bool:
    """Returns whether it actually sent successfully -- callers that need
    to know (e.g. orchestrator's daily give-back breaker, which reports
    exactly which symbols got closed on the real side) can check this
    instead of assuming success.

    [ADD 2026-08-25] `source` identifies which caller asked for this close
    -- same reasoning as pineconnector_relay.send_close's own 2026-08-25
    note. TradeSgnl currently has only one caller (normal exits), but
    tagging it here too keeps both relays' diagnostics symmetric."""
    if not TRADESGNL_LICENSE_ID:
        return False
    if trade["id"] not in _confirmed_open_ids:
        print(f"[tradesgnl_relay] SKIPPED close (source={source}) for trade {trade['id']} ({trade['symbol']}) -- "
              f"id not in _confirmed_open_ids (see seed_confirmed_ids' 2026-08-17 GBPJPY incident note); "
              f"if a real position is actually still open, it is now stranded")
        return False  # entry was never confirmed-sent -- nothing real to close
    _confirmed_open_ids.discard(trade["id"])
    _relayed_partial_ids.discard(trade["id"])
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    close_action = "closelong" if is_long else "closeshort"
    command = f"{TRADESGNL_LICENSE_ID},{symbol},{close_action},comment={_comment_id(symbol, trade['side'])}"
    sent = await asyncio.to_thread(_send_sync, command)
    print(f"[tradesgnl_relay] close source={source} trade={trade['id']} ({symbol}) sent={sent}")
    if not sent:
        from engine import discord_alerts
        await discord_alerts.alert_relay_failure(symbol, "close", command)
    return sent
