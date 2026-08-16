"""Session-currency dominance — from the forex-sessions research notes,
not present in V109 or the existing Forex/Forex engine. Rewards a pair
where exactly one of its two currencies is dominant in the currently
active session (a clean directional push); penalizes pairs where both
currencies are simultaneously dominant (e.g. AUDJPY during the Asian
session — the "battle between two active currencies" case, murkier price
action). Used as a Layer-2 confluence factor, not a Layer-1 hard gate,
since V109's own per-pair session windows already gate whether a pair
trades at all in a given hour.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from config import SESSIONS_UTC, SESSION_DOMINANT_CURRENCIES
from engine.fx_conversion import base_currency, quote_currency


def current_session(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    for name, (start, end) in SESSIONS_UTC.items():
        if start <= hour < end:
            return name
    return "offsession"


def dominance_score(symbol: str, now: Optional[datetime] = None) -> Dict:
    session = current_session(now)
    dominant = SESSION_DOMINANT_CURRENCIES.get(session, frozenset())
    base_dom = base_currency(symbol) in dominant
    quote_dom = quote_currency(symbol) in dominant

    if base_dom != quote_dom:  # exactly one dominant -> clean directional push
        clarity, score = "clean", 1.0
    elif base_dom and quote_dom:  # both dominant -> currencies fighting each other
        clarity, score = "battle", 0.0
    else:  # neither dominant -> a quiet pair for this session, not a battle either
        clarity, score = "quiet", 0.3

    return {
        "session": session,
        "base_dominant": base_dom,
        "quote_dominant": quote_dom,
        "clarity": clarity,
        "score": score,
    }
