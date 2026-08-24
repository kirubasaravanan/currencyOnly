"""PineConnector webhook relay — ADDITIONAL real order execution, parallel
to tradesgnl_relay.py, not a replacement for it. Mirrors that module's
shape and safety patterns exactly (same WIDE_TP trick, same confirmed-
open-ids tracking, same no-op-until-configured convention) but speaks
PineConnector's own command grammar instead of TradeSgnl's.

[ADD 2026-08-19, explicit user instruction] User's plan: TradeSgnl stays
live and untouched; this is a second, independent real connection running
alongside it from the same paper trades, not a swap. Motivation (user's
own observation from watching real fills, not something confirmed in this
codebase): TradeSgnl appears to fill the entry first and attach SL/TP a
moment later, occasionally leaving a real position briefly unprotected
during high-volatility/news timing; PineConnector's own command grammar
sets sl=/tp= in the SAME entry command as the buy/sell, so there's no gap.
Ported from the sister Forex/Forex app's engine/pineconnector_relay.py —
that module's own docstring is explicit that PineConnector's grammar has
"reportedly shifted across versions" and was "NEITHER... checked against
a live PineConnector reference or real fill yet". That turned out to be
right to be cautious about: the ported lots=/sl=/tp= parameter names were
WRONG. [FIX 2026-08-19, found live] Confirmed against a real, already-
working entry command from the sister app's own live gold bridge (same
license/account, comment=pineconnector_gold-XAUUSD-L, actually filled)
that the real parameter names are vol_lots=/sl_price=/tp_price=, not
lots=/sl=/tp= -- explains every earlier test-fire failure on this account
(the EA was never reading our lot value at all under the wrong key,
not a Volume-Type-setting problem as first suspected). Close command
grammar confirmed live too, same test session: closelong/closeshort +
comment= works exactly as originally ported -- BUT PineConnector matches
the close to its position by exact comment string ("Position will not be
closed since comments differ" was the actual EA error hit mid-test, from
deliberately using a different comment on a manual test-close).
_comment_id(symbol, side) is deterministic and called identically by both
send_entry() and send_close() below, so real trades always match
automatically -- this constraint only bit the manual test. Worth
remembering if this ever needs manual/ad-hoc testing again: the close
comment MUST be byte-identical to the entry's.

Command grammar (LICENSE,ACTION,SYMBOL field order -- TradeSgnl's is
LICENSE,SYMBOL,ACTION, genuinely different, not a typo -- with
vol_lots=/sl_price=/tp_price= parameter names, confirmed live 2026-08-19):
    entry: {license},{buy|sell},{symbol},vol_lots={lots},sl_price={sl},tp_price={tp},comment={id}
    close: {license},{closelong|closeshort},{symbol},comment={id}  -- confirmed live, comment must match entry's exactly
No documented partial-close or SL/TP-modify command, same limitation as
TradeSgnl -- so the same WIDE_TP_MULTIPLIER workaround from
tradesgnl_relay.py applies here too: entry sends a deliberately
unreachable TP so the broker-side pending order never fires on its own,
and paper's own partial/close commands (via send_partial_close/send_close
below) are the only thing that ever closes or trims the real position.
This is about the exit-management gap (no modify command), which is
separate from the entry-protection gap (atomic fill) this relay was
actually added to fix -- both TradeSgnl and PineConnector need the wide-TP
treatment regardless of how atomic their entry fill is.

No-ops automatically whenever PINECONNECTOR_LICENSE_ID is unset in .env,
same convention as tradesgnl_relay.py -- safe to leave sitting in the repo
with zero live effect until real credentials are provided.
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

PINECONNECTOR_LICENSE_ID = os.getenv("PINECONNECTOR_LICENSE_ID", "")
PINECONNECTOR_WEBHOOK_URL = os.getenv("PINECONNECTOR_WEBHOOK_URL", "")  # no known default -- must be set per account

_last_send_ts = 0.0
_MIN_INTERVAL_SECONDS = 1.0

# Same reasoning as tradesgnl_relay.py's own WIDE_TP_MULTIPLIER -- see that
# module's docstring for the full explanation. PineConnector's grammar has
# no documented modify/partial-close command either, so the broker-side
# pending TP needs to be pushed far enough out that it never fires before
# paper's own trailing-stop logic sends an explicit close/partial-close.
WIDE_TP_MULTIPLIER = 20.0

_confirmed_open_ids: Set[int] = set()
_relayed_partial_ids: Set[int] = set()


def _fmt_price(symbol: str, price: float) -> str:
    pip = PIP_SIZE.get(symbol, 0.0001)
    decimals = 3 if pip >= 0.01 else 5
    return f"{price:.{decimals}f}"


def _comment_id(symbol: str, side: str) -> str:
    is_long = side in ("BUY", "BULLISH")
    return f"pineconnector-{symbol}-{'L' if is_long else 'S'}"


def _send_sync(command: str) -> bool:
    global _last_send_ts
    if not PINECONNECTOR_LICENSE_ID or not PINECONNECTOR_WEBHOOK_URL:
        return False  # not configured -- no-op, matching tradesgnl_relay.py's own convention
    elapsed = time.time() - _last_send_ts
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    try:
        r = requests.post(
            PINECONNECTOR_WEBHOOK_URL, data=command,
            headers={"Content-Type": "text/plain"}, timeout=10,
        )
        _last_send_ts = time.time()
        if r.status_code in (200, 201, 202, 204):
            print(f"[pineconnector_relay] SENT ({r.status_code}): {command} | response: {r.text[:200]}")
            return True
        print(f"[pineconnector_relay] webhook returned {r.status_code}: {r.text[:200]} | command was: {command}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[pineconnector_relay] webhook error: {exc}")
        return False


def _wide_tp_price(trade: Dict) -> float:
    entry = trade["entry_price"]
    return entry + WIDE_TP_MULTIPLIER * (trade["tp2_price"] - entry)


def seed_confirmed_ids(open_trades: list) -> None:
    """Same restart-persistence fix as tradesgnl_relay.py's own
    seed_confirmed_ids -- call once at orchestrator startup so a process
    restart doesn't strand already-open positions' closes/partials."""
    for t in open_trades:
        _confirmed_open_ids.add(t["id"])
        if t.get("partial_taken"):
            _relayed_partial_ids.add(t["id"])


async def send_entry(trade: Dict) -> None:
    if not PINECONNECTOR_LICENSE_ID:
        return
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    action = "buy" if is_long else "sell"
    tp_to_send = trade["tp_price"] if config.state.exit_mode == "static" else _wide_tp_price(trade)
    command = (
        f"{PINECONNECTOR_LICENSE_ID},{action},{symbol},"
        f"vol_lots={trade['lots']:.2f},"
        f"sl_price={_fmt_price(symbol, trade['sl_price'])},"
        f"tp_price={_fmt_price(symbol, tp_to_send)},"
        f"comment={_comment_id(symbol, trade['side'])}"
    )
    sent = await asyncio.to_thread(_send_sync, command)
    if sent:
        _confirmed_open_ids.add(trade["id"])
    else:
        from engine import discord_alerts
        await discord_alerts.alert_relay_failure(symbol, "entry", command, relay="pineconnector")


async def send_partial_close(trade: Dict) -> bool:
    """[FIX 2026-08-19, found live] Confirmed against PineConnector's own
    official syntax docs (docs.pineconnector.com/syntax) AND a live test-
    fire: partial close is a DISTINCT action verb (closelongvol/
    closeshortvol), not a parameter added to closelong/closeshort (that
    was tried first and just fully closed the position instead -- no
    partial reduction at all). The volume to close is passed via risk= --
    confusingly reusing that key name, but confirmed live it means "lots
    to close" here, not risk sizing (docs: "The risk= parameter in partial
    closes represents 'the volume to close' rather than risk exposure").
    Live-tested: a 0.20-lot position sent risk=0.10 correctly reduced to
    0.10 lots remaining, not a full close. Note this is the exact
    "closelongvol/closeshortvol" grammar that turned out to be WRONG when
    guessed for TradeSgnl (see that module's own 2026-08-17 fix note) --
    right for this platform, wrong for that one, a reminder these two
    relays' grammars never transfer to each other."""
    if not PINECONNECTOR_LICENSE_ID:
        return False
    if trade["id"] not in _confirmed_open_ids:
        print(f"[pineconnector_relay] SKIPPED partial-close for trade {trade['id']} ({trade['symbol']}) -- "
              f"id not in _confirmed_open_ids; real position may now be desynced from paper")
        return False
    if trade["id"] in _relayed_partial_ids:
        return False
    _relayed_partial_ids.add(trade["id"])
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    partial_action = "closelongvol" if is_long else "closeshortvol"
    volume_closed = round(trade.get("original_lots", trade["lots"]) - trade["lots"], 2)
    command = (
        f"{PINECONNECTOR_LICENSE_ID},{partial_action},{symbol},"
        f"risk={volume_closed:.2f},"
        f"comment={_comment_id(symbol, trade['side'])}"
    )
    sent = await asyncio.to_thread(_send_sync, command)
    if not sent:
        from engine import discord_alerts
        await discord_alerts.alert_relay_failure(symbol, "partial_close", command, relay="pineconnector")
    return sent


async def send_close(trade: Dict) -> bool:
    if not PINECONNECTOR_LICENSE_ID:
        return False
    if trade["id"] not in _confirmed_open_ids:
        print(f"[pineconnector_relay] SKIPPED close for trade {trade['id']} ({trade['symbol']}) -- "
              f"id not in _confirmed_open_ids; if a real position is actually still open, it is now stranded")
        return False
    _confirmed_open_ids.discard(trade["id"])
    _relayed_partial_ids.discard(trade["id"])
    symbol = trade["symbol"]
    is_long = trade["side"] in ("BUY", "BULLISH")
    close_action = "closelong" if is_long else "closeshort"
    command = f"{PINECONNECTOR_LICENSE_ID},{close_action},{symbol},comment={_comment_id(symbol, trade['side'])}"
    sent = await asyncio.to_thread(_send_sync, command)
    if not sent:
        from engine import discord_alerts
        await discord_alerts.alert_relay_failure(symbol, "close", command, relay="pineconnector")
    return sent
