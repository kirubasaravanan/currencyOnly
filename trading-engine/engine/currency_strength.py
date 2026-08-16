"""Currency strength ranking: measures each currency's relative momentum
across every pair it appears in, so a candidate trade can be scored higher
when it pairs a genuinely strong currency against a genuinely weak one.
The same "clean vs. battling" idea as session_dominance.py, but driven by
measured price momentum instead of session scheduling — complementary,
not a replacement: one is about WHEN a currency is typically active, this
is about WHAT it's actually doing right now.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from engine.fx_conversion import base_currency, quote_currency

STRENGTH_LOOKBACK_BARS = 50
CLEAN_SPREAD_PCT = 0.5  # % return separation treated as a fully clean divergence


def compute_ranking(frames_1h: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    """frames_1h: {symbol: 1h OHLC dataframe}. Returns {currency: strength}
    — the average % return contributed by that currency across every pair
    it appears in over the last STRENGTH_LOOKBACK_BARS bars. Positive means
    rallying, negative means weakening. A pair's return counts +for its
    base currency and -for its quote currency (price rising means the base
    strengthened against the quote)."""
    contributions: Dict[str, List[float]] = {}
    for symbol, df in frames_1h.items():
        if df is None or len(df) < STRENGTH_LOOKBACK_BARS + 1:
            continue
        window = df["close"].tail(STRENGTH_LOOKBACK_BARS + 1)
        pct_return = (float(window.iloc[-1]) / float(window.iloc[0]) - 1.0) * 100.0
        base, quote = base_currency(symbol), quote_currency(symbol)
        contributions.setdefault(base, []).append(pct_return)
        contributions.setdefault(quote, []).append(-pct_return)
    return {ccy: sum(vals) / len(vals) for ccy, vals in contributions.items()}


def strength_factor(symbol: str, direction: str, ranking: Optional[Dict[str, float]]) -> float:
    """1.0 when the trade pairs a strong currency against a weak one in the
    trade's own direction (clean directional setup); 0.0 when both
    currencies are similarly ranked (a 'battle', prone to ranging/chop);
    scaled smoothly in between. Returns a neutral 0.5 when no ranking is
    available (e.g. isolated calls without cross-pair context) rather than
    rewarding or penalizing blindly."""
    if not ranking:
        return 0.5
    base, quote = base_currency(symbol), quote_currency(symbol)
    base_rank, quote_rank = ranking.get(base), ranking.get(quote)
    if base_rank is None or quote_rank is None:
        return 0.5
    spread = base_rank - quote_rank  # positive -> base currently stronger than quote
    if direction == "bearish":
        spread = -spread  # for a sell, we want the quote currency to be the strong one
    return max(0.0, min(1.0, spread / CLEAN_SPREAD_PCT))
