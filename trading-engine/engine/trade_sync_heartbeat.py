"""Trade sync heartbeat — bidirectional reconciliation between the paper
broker's trade ledger and BOTH real MT5 accounts this app relays to
(TradeSgnl demo, license 3116556667670, login 110875560; PineConnector/
FundedNext real challenge, login from FUNDEDNEXT_MT5_LOGIN).

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

[UPGRADED 2026-08-21, explicit user instruction] Was alert-only in both
directions since 2026-08-17. The user asked directly whether it actually
does anything about a confirmed desync, and pointed out the honest answer
("no, alert-only") wasn't good enough: since a real position can never be
safely "reopened," the only sound fix is to bring BOTH sides down to
whichever state is already confirmed-true, never to guess or fabricate one
up. So now:
  - Direction 1 (paper open, real CONFIRMED closed via a positively-matched
    closing deal in MT5's own history): closes the PAPER trade to match,
    using the real deal's own exit price.
  - Direction 2 (paper closed, real still CONFIRMED open via a live
    positions_get() read): sends a close for the REAL position to match
    paper, then re-verifies it's actually gone before reporting success —
    never trusts the relay's "sent" response alone, same "verify, don't
    trust" convention as close_all_real_positions() in orchestrator.py.
Both directions still only ever act on a CONFIRMED state — "uncertain"
(missing-from-a-live-read only, no positively-matched deal) is still
reported, never acted on. That distinction is deliberate, not timidity:
the sister app's 2026-07-29 incident conflated "missing from a live read"
with "confirmed closed" and force-closed 28 genuinely-open positions at a
fabricated $0.00 during a connection outage. Acting on a POSITIVE match
(a real closing deal found in history, or a real position genuinely alive
in a fresh positions_get() read) carries none of that risk — it's not an
inference from absence, it's reading what already, definitely happened.

Extended 2026-08-21 to cover FundedNext alongside TradeSgnl (previously
TradeSgnl-only) — the real account had ZERO sync monitoring before this,
despite being the one with actual financial consequences. FundedNext's
account is shared with the sister Forex app's gold bridge (comment prefix
"pineconnector_gold-"), so its real-position reads are filtered to our own
tag prefix ("pineconnector-") — gold positions are invisible to this
module by construction, same principle as real_giveback_source.py.

Also added 2026-08-21: lot-size (partial-close) mismatch detection for
positions open on both sides under the same tag — the case where paper
took its 50%-at-TP1 partial but the real relay's partial-close command
silently failed (or vice versa), which the old binary open/closed matching
could never catch. DETECTION ONLY for now, not auto-corrected — reported
via Discord so real frequency/pattern can be seen before deciding how to
safely reduce the oversized side down to match (the same "only ever
reduce toward a confirmed number, never fabricate one" principle would
apply, but sourcing the correct partial-fill price needs more care than a
same-day pass should rush).

Grace period: any reconnect failure resets a "distrust findings" window,
so a flapping MT5 connection doesn't get read as a wave of real problems.
"""

from __future__ import annotations

import asyncio
import functools
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional

import config
from config import PAIRS
from engine.paper_broker import broker
from engine.real_giveback_source import FUNDEDNEXT_MT5_TERMINAL_PATH, FUNDEDNEXT_MT5_LOGIN
from engine.tradesgnl_relay import _comment_id as _tradesgnl_comment_id, send_close as _tradesgnl_send_close
from engine.pineconnector_relay import _comment_id as _pineconnector_comment_id, send_close as _pineconnector_send_close
from engine import direct_mt5_relay

# This demo account's server clock runs 3h ahead of true UTC (empirically
# confirmed 2026-08-17 for TradeSgnl, and independently re-confirmed
# 2026-08-20 for FundedNext too -- same value, different broker,
# coincidence not an assumption; see real_giveback_source.py's docstring).
MT5_SERVER_UTC_OFFSET = timedelta(hours=3)

HEARTBEAT_ENABLED = True

# [PAUSED 2026-08-25, explicit user instruction, found live] A real false
# positive: GBPCAD's partial-close left the real remainder open at 0.25
# lots on TradeSgnl, but the broker clears the position's comment the
# moment its volume changes. Direction 1's "genuinely open" check matched
# on (symbol, comment) -- once the comment went blank, that match broke,
# fell through to _find_closing_deal(), found the PARTIAL's own closing
# deal (never checked whether it accounted for the FULL remaining volume),
# and force-closed paper on a close that never actually happened. The real
# 0.25 lots sat open and unmanaged. Root cause fixed below (symbol-alone
# matching, no longer comment-dependent) -- this flag stays False until
# that fix has run clean for a while with no repeat false positives.
# Detection/alerting still runs either way; only the CLOSE ACTIONS are
# gated by this.
HEARTBEAT_AUTO_ACTIONS_ENABLED = False

HEARTBEAT_INTERVAL_SECONDS = 5 * 60
GRACE_PERIOD_SECONDS = 10 * 60
DIRECTION2_LOOKBACK_HOURS = 24
DIRECTION2_REALERT_SECONDS = 60 * 60
UNCERTAIN_ALERT_STREAK = 3  # 3 x 5min cadence = ~15 minutes of persistence

# MT5 volumes round to 0.01 lots -- this guards against float noise, not
# real mismatches.
LOT_MISMATCH_TOLERANCE = 0.011

# Same value as orchestrator.py's CLOSE_VERIFY_DELAY_SECONDS -- kept as its
# own constant here to avoid importing orchestrator.py (it imports this
# module, not the other way around).
CLOSE_VERIFY_DELAY_SECONDS = 5


@dataclass
class _AccountState:
    label: str
    terminal_path: str
    account_login: int
    comment_id_fn: Callable[[str, str], str]
    send_close: Callable[..., "Awaitable[bool]"]  # (trade, source=...) -- see 2026-08-25 source-tagging note
    # Set only for accounts that can hold positions that AREN'T ours (the
    # FundedNext terminal also carries the sister app's gold bridge) --
    # None means "every open position on this account is ours," no filter.
    own_tag_prefix: Optional[str] = None

    # [ADD 2026-08-26, explicit user instruction -- direct-MT5 execution
    # infrastructure] Per-account now, not a shared module constant --
    # MT5_SERVER_UTC_OFFSET below was independently empirically confirmed
    # for both TradeSgnl and FundedNext ("coincidence, not an assumption"
    # per that constant's own comment); a new broker isn't guaranteed to
    # share it. Default preserves today's behavior for the two existing
    # accounts with zero edits to either construction below.
    server_utc_offset: timedelta = field(default_factory=lambda: MT5_SERVER_UTC_OFFSET)

    last_unreachable_at: Optional[float] = None
    uncertain_streak: Dict[int, int] = field(default_factory=dict)
    direction2_last_alerted: Dict[int, float] = field(default_factory=dict)

    def to_true_utc(self, server_ts: float) -> datetime:
        return datetime.fromtimestamp(server_ts, tz=timezone.utc) - self.server_utc_offset

    def to_server_utc(self, true_utc: datetime) -> datetime:
        return true_utc + self.server_utc_offset

    def in_grace_period(self) -> bool:
        return self.last_unreachable_at is not None and (time.time() - self.last_unreachable_at) < GRACE_PERIOD_SECONDS

    def mark_unreachable(self) -> None:
        self.last_unreachable_at = time.time()

    def terminal_running(self) -> bool:
        """True only if the terminal is already running on its own --
        mt5.initialize() will silently LAUNCH it otherwise, which this
        heartbeat must never do (it should only ever piggyback on a
        terminal already open for real trading)."""
        import psutil
        try:
            for proc in psutil.process_iter(["name", "exe"]):
                if proc.info["name"] == "terminal64.exe" and proc.info["exe"] == self.terminal_path:
                    return True
            return False
        except Exception:  # noqa: BLE001
            return False

    def connect(self, mt5mod) -> bool:
        if not mt5mod.initialize(path=self.terminal_path):
            return False
        acc = mt5mod.account_info()
        return acc is not None and acc.login == self.account_login

    def is_ours(self, comment: str, symbol: Optional[str] = None) -> bool:
        """[FIX 2026-08-25] A blank comment used to fail this check outright
        on FundedNext (own_tag_prefix set), silently dropping a genuinely-
        ours position from real_positions the moment MT5 cleared its
        comment after a partial-close -- the same root cause as the
        (symbol, comment) matching bug above, but one filtering step
        earlier, and NOT fixed by the symbol-alone matching change alone
        since a position filtered out here never reaches that matching at
        all. A blank comment is treated as ours when the symbol is one
        this app trades -- safe because the gold bridge sharing this
        terminal only ever trades XAUUSD, which is never in config.PAIRS."""
        if not self.own_tag_prefix:
            return True
        if comment.startswith(self.own_tag_prefix):
            return True
        return not comment and symbol in PAIRS


TRADESGNL_ACCOUNT = _AccountState(
    label="TradeSgnl",
    terminal_path=r"C:\Users\Administrator\Pictures\Third Mt5\terminal64.exe",
    account_login=110875560,
    comment_id_fn=_tradesgnl_comment_id,
    send_close=_tradesgnl_send_close,
    own_tag_prefix=None,
)

FUNDEDNEXT_ACCOUNT = _AccountState(
    label="FundedNext",
    terminal_path=FUNDEDNEXT_MT5_TERMINAL_PATH,
    account_login=FUNDEDNEXT_MT5_LOGIN,
    comment_id_fn=_pineconnector_comment_id,
    send_close=_pineconnector_send_close,
    own_tag_prefix="pineconnector-",
)

# [ADD 2026-08-26, explicit user instruction -- direct-MT5 execution
# infrastructure] _AccountState carries no assumption that there are
# exactly 2 accounts -- this generalizes the reconciliation heartbeat to
# any configured direct-MT5 account automatically. functools.partial
# binds each account's own send_close/comment_id_fn from the generic
# direct_mt5_relay module (which takes the account as an explicit
# parameter, unlike tradesgnl_relay.py/pineconnector_relay.py which are
# each hardwired to one account). While config.DIRECT_MT5_ACCOUNTS is
# empty (its default), DIRECT_ACCOUNT_STATES is [] and ALL_ACCOUNTS is
# unchanged from today -- run_heartbeat()'s existing loop needs no edit.
DIRECT_ACCOUNT_STATES: List[_AccountState] = [
    _AccountState(
        label=acct.label,
        terminal_path=acct.terminal_path,
        account_login=acct.account_login,
        comment_id_fn=functools.partial(direct_mt5_relay._comment_id, acct),
        send_close=functools.partial(direct_mt5_relay.send_close, acct),
        own_tag_prefix=acct.comment_prefix,
        server_utc_offset=timedelta(hours=acct.server_utc_offset_hours),
    )
    for acct in config.DIRECT_MT5_ACCOUNTS if acct.enabled
]

ALL_ACCOUNTS: List[_AccountState] = [TRADESGNL_ACCOUNT, FUNDEDNEXT_ACCOUNT, *DIRECT_ACCOUNT_STATES]


def _find_closing_deal(mt5mod, acct: "_AccountState", symbol: str, tag: str, opened_at_true_utc: datetime):
    """The real MT5 deal that closed this position, found via MT5's own
    position_id linkage between the opening deal (entry=0, tagged with our
    comment) and its closing deal (entry=1 -- closing legs never carry our
    comment, the broker overwrites it, e.g. "[tp 1.16249]"). Returns None
    if no matching opening deal is found (can't safely guess which closing
    deal belongs to it)."""
    now = datetime.now(timezone.utc)
    frm_server = acct.to_server_utc(opened_at_true_utc - timedelta(minutes=10))
    to_server = acct.to_server_utc(now)
    deals = mt5mod.history_deals_get(frm_server, to_server, group=f"*{symbol}*")
    if not deals:
        return None
    deals = sorted(deals, key=lambda d: d.time)
    opens = [d for d in deals if getattr(d, "entry", None) == 0 and d.comment == tag]
    if not opens:
        return None
    open_deal = min(opens, key=lambda d: abs(acct.to_true_utc(d.time).timestamp() - opened_at_true_utc.timestamp()))
    closes = [d for d in deals if getattr(d, "entry", None) == 1 and d.position_id == open_deal.position_id]
    if not closes:
        return None
    return closes[-1]


def _detect_sync(acct: _AccountState) -> Dict:
    """All MT5 I/O and decision-making for one account. Never raises -- a
    failure here must not take down the scan loop. Read-only: makes no
    changes itself, only reports what's found and what SHOULD be done --
    run_heartbeat_for() below performs the actual actions, since sending a
    real close command is an async webhook call that can't happen inside
    this blocking, thread-run function."""
    result: Dict = {
        "account": acct.label,
        "reachable": False,
        "confirmed_phantoms": [],
        "uncertain": [],
        "still_open_on_real": [],
        "lot_mismatches": [],
        "errors": [],
    }
    if not acct.account_login:
        result["errors"].append(f"{acct.label}: no account login configured -- nothing to reconcile against")
        return result
    if not acct.terminal_running():
        acct.mark_unreachable()
        result["errors"].append(f"{acct.label}: MT5 terminal not running -- skipped (never auto-launches it)")
        return result

    try:
        import MetaTrader5 as mt5
    except ImportError:
        result["errors"].append("MetaTrader5 package not installed")
        return result

    if not acct.connect(mt5):
        acct.mark_unreachable()
        result["errors"].append(f"{acct.label}: connect/verify failed: {mt5.last_error()}")
        return result

    result["reachable"] = True
    in_grace = acct.in_grace_period()

    try:
        # [FIX 2026-08-25, explicit user instruction] Matched by (symbol,
        # comment) until now -- broken by a real, observed broker behavior:
        # the comment gets cleared the moment a position's volume changes
        # (i.e. right after our own partial-close), so a genuinely-still-
        # open post-partial position stopped matching its own tag and fell
        # through into the "must be closed" path below. Symbol alone is
        # safe here specifically because this app only ever holds ONE
        # position per symbol at a time (orchestrator.py's own
        # open_symbols check enforces that), and on the shared FundedNext
        # terminal, acct.is_ours() already filters out the gold bridge's
        # positions by comment PREFIX before this point -- so a currencyOnly
        # FX symbol can never collide with the gold bridge's XAUUSD, whose
        # symbol never appears in this app's own PAIRS list anyway.
        real_positions = [p for p in (mt5.positions_get() or ()) if acct.is_ours(p.comment, p.symbol)]
        real_positions_by_symbol = {p.symbol: p for p in real_positions}
        internal_open_symbols = {t.get("symbol") for t in broker.open_positions}

        # --- Direction 1 (paper open, real not) + lot-mismatch check for
        # positions genuinely open on both sides ---
        still_uncertain_ids = set()
        for t in list(broker.open_positions):
            symbol = t.get("symbol")
            side = t.get("side", "")
            tag = acct.comment_id_fn(symbol, side)  # still used for _find_closing_deal()'s history search below
            real_pos = real_positions_by_symbol.get(symbol)
            if real_pos is not None:
                if abs(real_pos.volume - t.get("lots", 0.0)) > LOT_MISMATCH_TOLERANCE:
                    result["lot_mismatches"].append({
                        "trade_id": t["id"], "symbol": symbol, "side": side,
                        "paper_lots": t.get("lots"), "real_volume": real_pos.volume,
                        "real_ticket": real_pos.ticket,
                    })
                continue  # genuinely open on both sides

            opened_at_str = t.get("opened_at")
            try:
                opened_at = datetime.fromisoformat(opened_at_str) if opened_at_str else datetime.now(timezone.utc)
            except ValueError:
                opened_at = datetime.now(timezone.utc)
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)

            deal = _find_closing_deal(mt5, acct, symbol, tag, opened_at)
            if deal is None or in_grace:
                still_uncertain_ids.add(t["id"])
                streak = acct.uncertain_streak.get(t["id"], 0) + 1
                acct.uncertain_streak[t["id"]] = streak
                if streak >= UNCERTAIN_ALERT_STREAK:
                    result["uncertain"].append({
                        "symbol": symbol, "side": side,
                        "internal_pnl": t.get("pnl"),
                        "reason": "in_grace_period" if in_grace else "no_matching_closing_deal_found",
                        "consecutive_checks": streak,
                    })
                continue

            acct.uncertain_streak.pop(t["id"], None)
            result["confirmed_phantoms"].append({
                "trade_id": t["id"], "symbol": symbol, "side": side,
                "internal_pnl": t.get("pnl"),
                "real_exit_price": deal.price, "real_pnl": round(deal.profit, 2),
                "real_close_time_utc": acct.to_true_utc(deal.time).isoformat(),
            })

        # Clear streaks for any trade that's no longer open (closed, either
        # genuinely on both sides or via the confirmed-phantom path above)
        # or has resolved back to genuinely-open-on-both -- otherwise a
        # stale streak could immediately re-trigger an alert for a
        # DIFFERENT, unrelated later trade that happens to reuse the same id.
        for stale_id in list(acct.uncertain_streak):
            if stale_id not in still_uncertain_ids:
                acct.uncertain_streak.pop(stale_id, None)

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
                if symbol in internal_open_symbols:
                    # A newer paper position on this symbol is open right
                    # now -- the real position matches THAT one, not this
                    # older closed trade. Direction 1 above already covers
                    # whether the newer one is in sync.
                    continue
                real_pos = real_positions_by_symbol.get(symbol)
                if real_pos is None:
                    continue  # genuinely closed on both sides -- good
                last_alerted = acct.direction2_last_alerted.get(real_pos.ticket, 0.0)
                if time.time() - last_alerted < DIRECTION2_REALERT_SECONDS:
                    continue
                acct.direction2_last_alerted[real_pos.ticket] = time.time()
                result["still_open_on_real"].append({
                    "symbol": symbol, "side": t.get("side"),
                    "paper_trade": t,
                    "paper_closed_at": closed_at_str, "paper_close_reason": t.get("reason"),
                    "real_ticket": real_pos.ticket, "real_profit": round(real_pos.profit, 2),
                    "real_volume": real_pos.volume,
                })
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"{acct.label}: heartbeat check error: {exc}")
    finally:
        mt5.shutdown()

    return result


def _verify_ticket_closed_sync(acct: _AccountState, ticket: int) -> Optional[bool]:
    """True if the ticket is genuinely gone, False if still found open,
    None if unreachable (can't verify either way -- caller must not assume
    success from a None here, same "verify, don't trust" principle as
    orchestrator.py's own _positions_still_open_sync)."""
    if not acct.terminal_running():
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not acct.connect(mt5):
        return None
    try:
        positions = mt5.positions_get(ticket=ticket)
        return not positions
    except Exception:  # noqa: BLE001
        return None
    finally:
        mt5.shutdown()


async def run_heartbeat_for(acct: _AccountState, prices: Optional[Dict[str, float]] = None) -> Dict:
    """Async entry point for one account. Runs the blocking detection via
    asyncio.to_thread, then acts on CONFIRMED findings only -- see module
    docstring for exactly what "confirmed" means and why "uncertain"
    findings are still never acted on.

    prices: this cycle's live price snapshot (same dict orchestrator.py's
    _scan_once already has), passed through to broker.close_trade() so a
    direction-1 sync-close prices correctly on cross/JPY pairs instead of
    silently defaulting to a 1.0 USD conversion rate."""
    if not HEARTBEAT_ENABLED:
        return {"disabled": True}

    result = await asyncio.to_thread(_detect_sync, acct)
    result["direction1_closed_paper"] = []
    result["direction2_closed_real"] = []
    result["direction2_close_unverified"] = []
    result["actions_paused"] = not HEARTBEAT_AUTO_ACTIONS_ENABLED

    if not HEARTBEAT_AUTO_ACTIONS_ENABLED:
        # Detection/alerting above still ran in full -- confirmed_phantoms
        # and still_open_on_real are populated for visibility. Only the
        # actual close actions are held back while this is False.
        return result

    for phantom in result["confirmed_phantoms"]:
        closed = broker.close_trade(
            phantom["trade_id"], phantom["real_exit_price"],
            reason="real_sync_close", prices=prices,
        )
        if closed is not None:
            result["direction1_closed_paper"].append(phantom)

    for still_open in result["still_open_on_real"]:
        sent = await acct.send_close(still_open["paper_trade"], source="sync_heartbeat_direction2")
        if not sent:
            result["direction2_close_unverified"].append(still_open)
            continue
        await asyncio.sleep(CLOSE_VERIFY_DELAY_SECONDS)
        gone = await asyncio.to_thread(_verify_ticket_closed_sync, acct, still_open["real_ticket"])
        if gone:
            result["direction2_closed_real"].append(still_open)
        else:
            result["direction2_close_unverified"].append(still_open)

    return result


async def run_heartbeat(prices: Optional[Dict[str, float]] = None) -> Dict:
    """Runs both accounts. Returns {"TradeSgnl": {...}, "FundedNext": {...}}
    (or {"disabled": True} if HEARTBEAT_ENABLED is False)."""
    if not HEARTBEAT_ENABLED:
        return {"disabled": True}
    results: Dict = {}
    for acct in ALL_ACCOUNTS:
        results[acct.label] = await run_heartbeat_for(acct, prices)
    return results
