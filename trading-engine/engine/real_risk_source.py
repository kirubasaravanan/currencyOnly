"""Reads FundedNext's REAL, live open-position risk directly from MT5, to
gate new PineConnector entries against the account's actual "Max Risk: 3%
At any time" rule. [ADD 2026-08-21, explicit user instruction]

Why this exists: risk.py's own 1% position sizing is computed against
PAPER's equity (~$10.7k), not FundedNext's real equity (~$25.2k) -- the
exact same lot size gets relayed to PineConnector regardless of which
account it's sized against. Right now paper is smaller than FundedNext,
so the real percentage risked happens to land under 3% -- but that's a
coincidence of the current relative account sizes, not a designed
guarantee, and it drifts as paper's own equity moves independently of
FundedNext's. This module checks the REAL number directly instead of
trusting that coincidence to hold.

Whole-account, not ours-only: unlike real_giveback_source.py (which
filters TO our own "pineconnector-" comment tag for P&L purposes), this
module deliberately does NOT filter by tag -- it sums risk across EVERY
open position on the account, including the sister app's gold bridge
("pineconnector_gold-" prefix). The 3% rule is about the account's total
real exposure, not just what we personally opened.

Uses mt5.order_calc_profit() to compute each position's dollar risk-to-
stop (what it would lose if it hit its own SL right now) rather than
manually reimplementing contract-size/currency-conversion math -- lets
the broker's own pricing engine handle FX pairs and gold identically and
correctly, via the same call.

Read-only. Never closes, modifies, or places anything -- this module only
ever answers "what's the real aggregate open risk right now, and would
adding this trade push it over 3%," the orchestrator decides what to do
with that answer.

[DELIBERATE DEVIATION from this repo's usual fail-safe convention] Every
other real-data module here treats "can't reach MT5" as "skip this
cycle, don't act" -- which for something like the give-back breaker means
failing OPEN (new entries proceed as if the check never ran). This module
fails CLOSED instead: if it can't verify the real risk number, it blocks
the PineConnector send rather than assuming it's safe. The give-back
breaker's fail-open stance is fine for an opportunistic protection; this
one exists specifically to keep the account inside a real compliance
rule, so "unsure" should mean "don't risk it," not "proceed as before."
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from dotenv import load_dotenv

from engine.real_giveback_source import FUNDEDNEXT_MT5_TERMINAL_PATH, FUNDEDNEXT_MT5_LOGIN

load_dotenv()

MAX_RISK_PCT = float(os.getenv("FUNDEDNEXT_MAX_RISK_PCT", "3.0"))


def _terminal_running() -> bool:
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


def _position_risk_usd(mt5mod, pos) -> float:
    """Dollar loss if this position hit its own SL right now. Returns 0.0
    for a position with no SL set (nothing to compute a stop-based risk
    from) -- not counting it is the honest answer, not a fabricated one."""
    if not pos.sl:
        return 0.0
    order_type = mt5mod.ORDER_TYPE_BUY if pos.type == mt5mod.POSITION_TYPE_BUY else mt5mod.ORDER_TYPE_SELL
    profit = mt5mod.order_calc_profit(order_type, pos.symbol, pos.volume, pos.price_open, pos.sl)
    return abs(profit) if profit is not None else 0.0


def _check_sync(symbol: str, volume: float, entry_price: float, sl_price: float, is_long: bool) -> Optional[Dict]:
    """Synchronous -- call via asyncio.to_thread (MT5's API is blocking).
    Returns None on any failure/unreachability -- caller must fail CLOSED
    on None (see module docstring), the one place this repo's convention
    is deliberately inverted."""
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
            "projected_open_risk_usd": round(projected_risk, 2),
            "projected_open_risk_pct": round(projected_pct, 3),
            "would_exceed": projected_pct > MAX_RISK_PCT,
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        mt5.shutdown()


async def check_pineconnector_risk_ok(symbol: str, volume: float, entry_price: float, sl_price: float, is_long: bool) -> Optional[Dict]:
    """Async wrapper. None means unreachable -- caller must treat that as
    "block the send" (fail closed), not "assume it's fine" -- see module
    docstring for why this one deviates from the repo's usual fail-open
    convention for unreachable real-data checks."""
    import asyncio
    return await asyncio.to_thread(_check_sync, symbol, volume, entry_price, sl_price, is_long)
