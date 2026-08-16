"""Quote-currency-to-USD conversion for P&L, generalized for any pair in
the 17-pair universe (majors, USD-base pairs, and non-USD crosses like
GBPAUD/EURCAD/GBPNZD) rather than a hardcoded per-quote-currency table.
"""

from __future__ import annotations

from typing import Dict, Optional


def base_currency(symbol: str) -> str:
    return symbol[:3]


def quote_currency(symbol: str) -> str:
    return symbol[-3:]


def usd_conversion_rate(symbol: str, prices: Optional[Dict[str, float]]) -> Optional[float]:
    """1 unit of `symbol`'s quote currency, in USD. Returns None (never a
    silent 1.0 guess) if the cross-rate needed isn't in `prices` — callers
    must supply live rates for every currency involved or forgo conversion."""
    quote = quote_currency(symbol)
    if quote == "USD":
        return 1.0
    if prices is None:
        return None

    base = base_currency(symbol)
    if base == "USD":  # USDJPY/USDCHF/USDCAD — invert the pair's own rate
        rate = prices.get(symbol)
        return (1.0 / rate) if rate else None

    # Cross pair, neither leg is USD (GBPAUD, EURCAD, GBPNZD, NZDCAD, ...):
    # derive the quote currency's own USD rate from whichever direct pair
    # is available in `prices`.
    direct = f"{quote}USD"  # e.g. AUDUSD, NZDUSD, GBPUSD
    if prices.get(direct):
        return prices[direct]
    inverse = f"USD{quote}"  # e.g. USDCAD, USDJPY, USDCHF
    if prices.get(inverse):
        return 1.0 / prices[inverse]
    return None
