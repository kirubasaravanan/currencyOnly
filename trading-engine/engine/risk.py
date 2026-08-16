"""ATR-based position sizing (1% risk, clamped to per-pair lot bounds) and
account-wide daily/weekly loss + drawdown gating, aggregated across all 17
pairs (V109's own comments flag that Pine's per-chart daily caps don't
aggregate across pairs — a single Python engine fixes that structurally).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import (
    RISK_PER_TRADE_PCT,
    DAILY_LOSS_LIMIT,
    WEEKLY_LOSS_LIMIT,
    MAX_OPEN_TRADES,
    MAX_DRAWDOWN_PCT,
    LOT_BOUNDS,
    CONTRACT_SIZE_USD,
)
from engine.fx_conversion import usd_conversion_rate

IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_date(dt_str: str):
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + IST_OFFSET).date()


def position_size(symbol: str, equity: float, entry_price: float, sl_price: float,
                   size_multiplier: float = 1.0, prices: Optional[Dict[str, float]] = None) -> Dict:
    """size_multiplier (0.5x-1.5x, from entry.py's confluence_score — this
    app's own added factors, no longer gating entry at all, see entry.py's
    2026-08-16 fix note) scales the ATR-risk-sized lots up on strong
    confluence agreement and down on disagreement, applied BEFORE the
    per-pair lot-bounds clamp.

    [FIX 2026-08-16] sl_dist is in the PAIR'S OWN quote-currency price
    units, not USD — dividing risk_usd by it directly (without converting)
    was only correct for the 4 USD-quoted pairs (EURUSD/GBPUSD/AUDUSD/
    NZDUSD). For USD-base pairs and cross pairs this silently mis-sized
    every trade: USDJPY undersized by ~150x (1 JPY ≈ 1/150 USD), meaning
    those trades were never actually risking the intended 1% of equity —
    found from a direct user observation that flat per-lot commission
    against wildly different per-pair profit-per-lot only makes sense if
    sizing itself is already correct, which it wasn't. `prices` (the same
    dict already in scope at both call sites) supplies the cross-rates
    fx_conversion.usd_conversion_rate needs; falls back to 1.0 (flagged via
    conv_rate_unknown, not silently trusted) if a needed rate is missing."""
    sl_dist = abs(entry_price - sl_price)
    risk_usd = equity * (RISK_PER_TRADE_PCT / 100.0)
    contract = CONTRACT_SIZE_USD.get(symbol, 100_000.0)

    conv = usd_conversion_rate(symbol, prices)
    conv_rate_unknown = conv is None
    if conv is None:
        conv = 1.0

    raw_lots = (risk_usd / sl_dist) / (contract * conv) if sl_dist > 0 and conv > 0 else 0.0
    raw_lots *= size_multiplier
    min_lots, max_lots = LOT_BOUNDS.get(symbol, (0.01, 0.50))
    lots = max(min_lots, min(raw_lots, max_lots))
    return {
        "lots": round(lots, 2),
        "raw_lots": round(raw_lots, 4),
        "risk_usd": round(risk_usd, 2),
        "lot_size_reduced": raw_lots > max_lots,
        "conv_rate_unknown": conv_rate_unknown,
    }


def can_open_new_trade(open_trades: List[Dict], closed_trades: List[Dict], equity: float, peak_equity: float) -> Dict:
    if len(open_trades) >= MAX_OPEN_TRADES:
        return {"allowed": False, "reason": "max_open_trades"}

    drawdown_pct = ((peak_equity - equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
    if drawdown_pct >= MAX_DRAWDOWN_PCT:
        return {"allowed": False, "reason": "max_drawdown"}

    now_ist_date = (datetime.now(timezone.utc) + IST_OFFSET).date()

    today_closed = [t for t in closed_trades if t.get("closed_at") and _ist_date(t["closed_at"]) == now_ist_date]
    today_pnl = sum(t.get("pnl", 0.0) for t in today_closed)
    if peak_equity > 0 and today_pnl <= -(peak_equity * DAILY_LOSS_LIMIT / 100.0):
        return {"allowed": False, "reason": "daily_loss_limit"}

    week_ago = now_ist_date - timedelta(days=7)
    week_closed = [t for t in closed_trades if t.get("closed_at") and week_ago <= _ist_date(t["closed_at"]) <= now_ist_date]
    week_pnl = sum(t.get("pnl", 0.0) for t in week_closed)
    if peak_equity > 0 and week_pnl <= -(peak_equity * WEEKLY_LOSS_LIMIT / 100.0):
        return {"allowed": False, "reason": "weekly_loss_limit"}

    return {"allowed": True, "reason": None}
