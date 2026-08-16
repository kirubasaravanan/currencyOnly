"""Correlation filter generalized to net per-currency exposure — the
17-pair universe is heavy on non-USD crosses (GBPAUD, EURCAD, GBPNZD,
NZDCAD, EURGBP, CHFJPY), which a fixed USD-only group list can't express.
Each open position contributes +1/-1 to its base currency and -1/+1 to its
quote currency; caps the max open trades sharing a dominant currency
exposure in either direction.
"""

from __future__ import annotations

from typing import Dict, List

from config import MAX_TRADES_PER_CURRENCY_EXPOSURE
from engine.fx_conversion import base_currency, quote_currency


def net_currency_exposure(open_trades: List[Dict]) -> Dict[str, int]:
    exposure: Dict[str, int] = {}
    for t in open_trades:
        sign = 1 if t["side"] in ("BUY", "BULLISH") else -1
        base, quote = base_currency(t["symbol"]), quote_currency(t["symbol"])
        exposure[base] = exposure.get(base, 0) + sign
        exposure[quote] = exposure.get(quote, 0) - sign
    return exposure


def would_exceed_exposure(symbol: str, side: str, open_trades: List[Dict]) -> Dict:
    sign = 1 if side in ("BUY", "BULLISH") else -1
    base, quote = base_currency(symbol), quote_currency(symbol)
    exposure = net_currency_exposure(open_trades)

    projected_base = exposure.get(base, 0) + sign
    if abs(projected_base) > MAX_TRADES_PER_CURRENCY_EXPOSURE:
        return {"blocked": True, "currency": base, "exposure": projected_base}

    projected_quote = exposure.get(quote, 0) - sign
    if abs(projected_quote) > MAX_TRADES_PER_CURRENCY_EXPOSURE:
        return {"blocked": True, "currency": quote, "exposure": projected_quote}

    return {"blocked": False, "currency": None, "exposure": None}
