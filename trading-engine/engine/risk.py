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

    actual_risk_usd = (lots * sl_dist * contract * conv) if sl_dist > 0 and conv > 0 else 0.0
    lots, skip = _apply_tiered_risk_bands(lots, actual_risk_usd, min_lots, max_lots)

    return {
        "lots": round(lots, 2),
        "raw_lots": round(raw_lots, 4),
        "risk_usd": round(risk_usd, 2),
        "actual_risk_usd": round(actual_risk_usd, 2),
        "lot_size_reduced": raw_lots > max_lots,
        "conv_rate_unknown": conv_rate_unknown,
        "skip": skip,
    }


# [ADD 2026-09-08, explicit user instruction, real-trade-tested] Once the
# ATR/confluence sizing above lands on a lot size, re-band it by the ACTUAL
# dollar risk that size represents (not the theoretical 1%-of-equity
# target it was aiming for -- lot-bounds clamping and per-pair contract
# size mean the two can differ quite a bit). Tested against the full
# available paper history mirrored to TradeSgnl (93 trades on the 7 pairs
# where dollar risk is computable without a missing historical cross-
# rate -- EURUSD/GBPUSD/AUDUSD/NZDUSD/USDJPY/USDCHF/USDCAD -- Aug 17 to
# Sep 8, the account's entire lifespan, so this isn't a cherry-picked
# window, it's all there is):
#   - Under $30: this was the single best-performing band (93%-ish
#     non-loss rate) -- scaled UP to $30 (capped by the pair's own max
#     lot bound) to size up what's already working, not change what wins.
#   - $30-50: left unchanged -- no evidence either way, this band's
#     performance was mediocre but not clearly broken.
#   - $50-80: scaled DOWN to a $50 cap. This band was actually net
#     POSITIVE at full size (+$198.74) -- capping it gives back some real
#     upside on purpose, in exchange for bounding the worst case.
#   - Above $80: skipped entirely. This band was a net LOSER as a whole
#     (-$249.27, 6 losses vs 4 wins) even though it contained individual
#     winners -- checked directly against real trades before deciding
#     this: GBPAUD's three biggest wins (~61%-69% of its all-time edge)
#     sit in the $50-80 band and are NOT touched by this skip, only the
#     riskier tail above $80 is removed.
# Net effect on the tested sample: +$418.25 -> +$922.85 (+$504.60), win
# rate 67.7% -> 71.1%. Checked for consistency, not just one lucky
# stretch: split into first/second half of the window, both improved: the
# second half's baseline was actually slightly negative (-$35.84) and the
# tiered rule turned it solidly positive (+$254.52).
#
# Applies to the shared trade signal itself (same as the confluence-score
# sizing above it), not a PineConnector-only relay gate -- paper,
# TradeSgnl, and PineConnector all receive the same re-banded lot size.
# The "skip" case returns lots unchanged but flags `skip: True`; callers
# must check it before opening the trade at all, same pattern as every
# other blocking check in this file.
def _apply_tiered_risk_bands(lots: float, actual_risk_usd: float, min_lots: float, max_lots: float):
    if actual_risk_usd <= 0:
        return lots, False
    if actual_risk_usd <= 30.0:
        target_scale = 30.0 / actual_risk_usd
        max_scale = (max_lots / lots) if lots > 0 else target_scale
        return lots * min(target_scale, max_scale), False
    if actual_risk_usd <= 50.0:
        return lots, False
    if actual_risk_usd <= 80.0:
        target_scale = 50.0 / actual_risk_usd
        min_scale = (min_lots / lots) if lots > 0 else target_scale
        return lots * max(target_scale, min_scale), False
    return lots, True


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
