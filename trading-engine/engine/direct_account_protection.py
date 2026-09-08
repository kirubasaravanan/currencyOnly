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

from config import DirectMT5Account

IST_OFFSET = timedelta(hours=5, minutes=30)
MT5_SERVER_UTC_OFFSET = timedelta(hours=3)

_GIVEBACK_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "storage", "direct_giveback_state.json")


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
