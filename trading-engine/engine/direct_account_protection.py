"""Per-account real-money protection for direct-MT5 accounts -- the give-
back breaker and max-open-risk gate, generalized from the PineConnector-
only versions (engine/real_giveback_source.py, engine/real_risk_source.py)
to work off any config.DirectMT5Account instead of one hardcoded account.
[ADD 2026-09-08, explicit user instruction]

Deliberately a SEPARATE module, not a rewrite of the two PineConnector-
specific ones -- those keep protecting that real FundedNext account
exactly as before, untouched, zero regression risk. This module is
purely additive, the direct-MT5 counterpart.

Each DirectMT5Account carries its own giveback_min_peak/giveback_pct/
max_risk_pct (all Optional -- None disables that specific protection for
that specific account, same "blank keeps this inert" convention as every
other credential/threshold in this repo). Two accounts CAN reuse the same
numbers by just setting the same values, or diverge entirely -- nothing
here assumes a shared threshold.

Give-back state is tracked per-account (keyed by label, not a single
global flag like the PineConnector version) in
storage/direct_giveback_state.json, so multiple accounts each have
independent trigger state and one account's breaker firing never affects
another's.

Same fail-safe conventions as the two modules this generalizes:
  - Give-back reads are READ-ONLY and fail OPEN (unreachable == skip this
    cycle, don't act) -- an opportunistic protection, not a compliance
    rule.
  - The risk gate fails CLOSED (unreachable == block the entry) -- exists
    specifically to keep an account inside a real compliance limit, so
    "unsure" must mean "don't risk it."
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from config import COMMISSION_PER_LOT_PER_SIDE_USD, DirectMT5Account

IST_OFFSET = timedelta(hours=5, minutes=30)
MT5_SERVER_UTC_OFFSET = timedelta(hours=3)

# [ADD 2026-09-08, explicit user instruction: "yes align it to 5 AM IST"]
# The profit-lock feature specifically (NOT this account's giveback state,
# and NOT currencyOnly's own general _current_ist_date day-tracker in
# orchestrator.py, which stays plain-midnight-IST -- currencyOnly has no
# 5 AM reopen concept anywhere else in its config, unlike the Forex app)
# anchors "today" to 5 AM IST instead, matching the Forex app's own
# engine/profit_lock.py (itself matching account_daily_cap.py's
# _trading_day_ist, the boundary every OTHER daily cap in that codebase
# uses). This account's real P&L target is genuinely shared across both
# apps, so the day boundary needs to agree between them too -- otherwise
# the two apps could disagree about whether it's still "yesterday" or
# already "today" in the 12 AM-5 AM IST window, e.g. this app's pause
# flag resetting at midnight while the Forex app's stays held until 5 AM.
TRADING_DAY_START_HOUR_IST = 5


def _trading_day_ist(dt: datetime) -> str:
    """Calendar date (IST) of the 5AM-to-5AM trading day a timestamp falls
    in -- anything before 5:00 AM IST belongs to the PREVIOUS day's
    session. Exact same logic as the Forex app's account_daily_cap.py::
    _trading_day_ist, kept independent (not imported -- separate
    codebase, separate deployment) rather than shared."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist = dt.astimezone(timezone.utc) + IST_OFFSET
    if ist.hour < TRADING_DAY_START_HOUR_IST:
        ist = ist - timedelta(days=1)
    return ist.date().isoformat()


def trading_day_ist_now() -> str:
    return _trading_day_ist(datetime.now(timezone.utc))


_GIVEBACK_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "storage", "direct_giveback_state.json")
_PROFIT_LOCK_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "storage", "direct_profit_lock_state.json")


def _load_triggered_dates() -> Dict[str, str]:
    try:
        with open(os.path.abspath(_GIVEBACK_STATE_FILE)) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _save_triggered_date(label: str, date_str: str) -> None:
    path = os.path.abspath(_GIVEBACK_STATE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = _load_triggered_dates()
    data[label] = date_str
    with open(path, "w") as f:
        json.dump(data, f)


def is_giveback_triggered_today(account: DirectMT5Account, now_ist_date: str) -> bool:
    return _load_triggered_dates().get(account.label) == now_ist_date


def mark_giveback_triggered_today(account: DirectMT5Account, now_ist_date: str) -> None:
    _save_triggered_date(account.label, now_ist_date)


def _load_profit_lock_dates() -> Dict[str, str]:
    try:
        with open(os.path.abspath(_PROFIT_LOCK_STATE_FILE)) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _save_profit_lock_dates(data: Dict[str, str]) -> None:
    path = os.path.abspath(_PROFIT_LOCK_STATE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def is_profit_lock_paused_today(account: DirectMT5Account, now_ist_date: str) -> bool:
    return _load_profit_lock_dates().get(account.label) == now_ist_date


def mark_profit_lock_paused_today(account: DirectMT5Account, now_ist_date: str) -> None:
    data = _load_profit_lock_dates()
    data[account.label] = now_ist_date
    _save_profit_lock_dates(data)


def clear_profit_lock_paused_today(account: DirectMT5Account) -> bool:
    """Manual resume -- e.g. Discord's "!resumeprofit <label>". Removes
    this account's entry entirely (today's or any stale one) so new
    entries resume on the very next scan cycle. Returns whether there was
    actually anything to clear."""
    data = _load_profit_lock_dates()
    if account.label not in data:
        return False
    del data[account.label]
    _save_profit_lock_dates(data)
    return True


def _terminal_running(terminal_path: str) -> bool:
    import psutil
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            if proc.info["name"] == "terminal64.exe" and proc.info["exe"] == terminal_path:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _connect(mt5mod, account: DirectMT5Account) -> bool:
    if not mt5mod.initialize(path=account.terminal_path):
        return False
    acc = mt5mod.account_info()
    return acc is not None and acc.login == account.account_login


def _to_true_utc(account: DirectMT5Account, server_ts: float) -> datetime:
    return datetime.fromtimestamp(server_ts, tz=timezone.utc) - timedelta(hours=account.server_utc_offset_hours)


def _ist_date_str(dt: datetime) -> str:
    return (dt + IST_OFFSET).date().isoformat()


def _get_today_pnl_sync(account: DirectMT5Account) -> Optional[Dict]:
    """Same logic as real_giveback_source.py's own reader, parameterized.
    Never fabricates a number -- returns None whenever the account can't
    be verified reachable this cycle."""
    if not account.account_login:
        return None
    if not _terminal_running(account.terminal_path):
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not _connect(mt5, account):
        return None

    try:
        now_utc = datetime.now(timezone.utc)
        today = _ist_date_str(now_utc)
        frm_server = now_utc - timedelta(days=2) + timedelta(hours=account.server_utc_offset_hours)
        to_server = now_utc + timedelta(days=1) + timedelta(hours=account.server_utc_offset_hours)
        deals = mt5.history_deals_get(frm_server, to_server)
        if deals is None:
            return None

        our_position_ids = {
            d.position_id for d in deals
            if getattr(d, "entry", None) == 0 and str(d.comment).startswith(account.comment_prefix)
        }

        todays_exit_deals = []
        for d in deals:
            if getattr(d, "entry", None) != 1:
                continue
            if d.position_id not in our_position_ids:
                continue
            true_utc = _to_true_utc(account, d.time)
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
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        mt5.shutdown()


async def get_today_pnl_state(account: DirectMT5Account) -> Optional[Dict]:
    import asyncio
    return await asyncio.to_thread(_get_today_pnl_sync, account)


def _get_account_totals_sync(account: DirectMT5Account) -> Optional[Dict]:
    """Whole-ACCOUNT realized (today) + floating P&L -- every deal and
    open position on this login, regardless of which app or comment
    placed it. [ADD 2026-09-08, explicit user instruction: "even include
    gold also which is coming from other application... goal is to reach
    150 per day"]

    Deliberately NOT filtered by account.comment_prefix, unlike
    _get_today_pnl_sync() above -- that one answers "how did OUR bot do
    today", this one answers "how did the ACCOUNT do today", which is
    what a profit target shared across two independent apps (this one and
    the Forex/gold app, both trading the same FundedNext login) needs.
    Since MT5's own P&L is server-authoritative, either app can read this
    independently through its own terminal connection and both will see
    identical numbers -- no cross-app state sharing required.

    Floating P&L subtracts an ESTIMATED exit commission
    (config.COMMISSION_PER_LOT_PER_SIDE_USD per open lot) since the real
    account's own commission schedule isn't queryable directly -- same
    stand-in the paper broker uses elsewhere. Deliberately conservative:
    it slightly understates how much would actually be banked by closing
    now, erring toward NOT treating it as safe to pause too early.

    Same fail-open convention as get_today_pnl_state(): returns None
    whenever the account can't be verified reachable this cycle, never a
    fabricated number."""
    if not _terminal_running(account.terminal_path):
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not _connect(mt5, account):
        return None

    try:
        now_utc = datetime.now(timezone.utc)
        today = _trading_day_ist(now_utc)  # 5 AM IST boundary -- see module-level note
        frm_server = now_utc - timedelta(days=2) + timedelta(hours=account.server_utc_offset_hours)
        to_server = now_utc + timedelta(days=1) + timedelta(hours=account.server_utc_offset_hours)
        deals = mt5.history_deals_get(frm_server, to_server)
        if deals is None:
            return None

        realized = 0.0
        for d in deals:
            if getattr(d, "entry", None) != 1:  # exits only
                continue
            true_utc = _to_true_utc(account, d.time)
            if _trading_day_ist(true_utc) != today:
                continue
            realized += d.profit + d.commission + d.swap

        positions = mt5.positions_get() or ()
        floating_gross = sum(p.profit + p.swap for p in positions)
        est_exit_commission = sum(p.volume for p in positions) * COMMISSION_PER_LOT_PER_SIDE_USD
        floating = floating_gross - est_exit_commission

        return {
            "reachable": True,
            "realized": round(realized, 2),
            "floating": round(floating, 2),
            "combined": round(realized + floating, 2),
            "open_position_count": len(positions),
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        mt5.shutdown()


async def get_account_totals(account: DirectMT5Account) -> Optional[Dict]:
    import asyncio
    return await asyncio.to_thread(_get_account_totals_sync, account)


async def check_profit_lock_trigger(account: DirectMT5Account) -> Optional[Dict]:
    """Evaluate whether account.profit_lock_target has been reached
    safely enough to pause new entries for the rest of the IST day.
    [ADD 2026-09-08, explicit user instruction]

    Pause when: (realized + floating), whole account, >= profit_lock_target.

    [CORRECTED 2026-09-08, explicit user instruction/worked example:
    "suppose my pnl realised is 120 and ongoing trade 3 trade with
    combine unrealised is 20 the system should not do anything and the
    moment the unrealised crossed 30 the total pnl realised and unrealised
    is 150+ and it should pause"] The earlier version additionally
    required realized ALONE to already reach the target before this
    combined check even applied -- which would NOT have paused in that
    exact example (realized $120 never reaches $150 on its own, even once
    combined crosses it via floating gains). That earlier "realized >=
    target" gate is gone; combined crossing the target is sufficient by
    itself, exactly as the example describes.

    Known edge case, not guarded against (flagged, not fixed, since it
    wasn't part of what was asked): a day where realized is NEGATIVE and
    a single large floating position is what pushes combined >= target
    would also pause here, even though nothing has actually been banked
    yet and that floating gain could evaporate. Worth a guard (e.g.
    require realized >= 0) if that scenario turns out to matter in
    practice.

    Returns None (do nothing) if unreachable this cycle -- same fail-open
    convention as the giveback reader; this is an opportunistic lock, not
    a compliance rule, so a stale reading should never block trading."""
    if account.profit_lock_target is None:
        return None
    totals = await get_account_totals(account)
    if totals is None:
        return None
    should_pause = totals["combined"] >= account.profit_lock_target
    return {**totals, "target": account.profit_lock_target, "should_pause": should_pause}


def _position_risk_usd(mt5mod, pos) -> float:
    if not pos.sl:
        return 0.0
    order_type = mt5mod.ORDER_TYPE_BUY if pos.type == mt5mod.POSITION_TYPE_BUY else mt5mod.ORDER_TYPE_SELL
    profit = mt5mod.order_calc_profit(order_type, pos.symbol, pos.volume, pos.price_open, pos.sl)
    return abs(profit) if profit is not None else 0.0


def _check_risk_sync(account: DirectMT5Account, symbol: str, volume: float,
                      entry_price: float, sl_price: float, is_long: bool) -> Optional[Dict]:
    """Same logic as real_risk_source.py's own entry-gate check,
    parameterized. Fails CLOSED (None) on any unreachability -- caller
    must treat None as "block the send", see module docstring."""
    if account.max_risk_pct is None:
        return {"reachable": True, "would_exceed": False, "disabled": True}
    if not account.account_login:
        return None
    if not _terminal_running(account.terminal_path):
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not _connect(mt5, account):
        return None
    try:
        acc = mt5.account_info()
        equity = acc.equity
        positions = mt5.positions_get() or ()
        current_risk = sum(_position_risk_usd(mt5, p) for p in positions)

        order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL
        new_trade_profit = mt5.order_calc_profit(order_type, symbol, volume, entry_price, sl_price)
        new_trade_risk = abs(new_trade_profit) if new_trade_profit is not None else 0.0

        projected_risk = current_risk + new_trade_risk
        projected_pct = (100.0 * projected_risk / equity) if equity > 0 else 100.0

        return {
            "reachable": True,
            "equity": round(equity, 2),
            "current_open_risk_usd": round(current_risk, 2),
            "new_trade_risk_usd": round(new_trade_risk, 2),
            "projected_open_risk_pct": round(projected_pct, 3),
            "would_exceed": projected_pct > account.max_risk_pct,
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        mt5.shutdown()


async def check_account_risk_ok(account: DirectMT5Account, symbol: str, volume: float,
                                 entry_price: float, sl_price: float, is_long: bool) -> Optional[Dict]:
    import asyncio
    return await asyncio.to_thread(_check_risk_sync, account, symbol, volume, entry_price, sl_price, is_long)
