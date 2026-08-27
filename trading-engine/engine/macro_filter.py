"""Forex Factory economic calendar + tiered news blackout (symbol-agnostic
— reused as-is) plus a lightweight USD-direction read (DXY + US10Y) for
macro context. This replaces the existing app's gold-specific "bullish/
bearish for gold" bias field with a plain USD-direction framing, since
that's what's actually relevant for an FX-only app.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd
import yfinance as yf

from config import NEWS_BLACKOUT_MINUTES

FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_CACHE_TTL_SECONDS = 600




class EconomicCalendar:
    def __init__(self) -> None:
        self._events: List[Dict] = []
        self._last_fetch = 0.0

    def refresh(self) -> None:
        if self._events and time.time() - self._last_fetch < CALENDAR_CACHE_TTL_SECONDS:
            return
        self._last_fetch = time.time()
        try:
            req = urllib.request.Request(FOREX_FACTORY_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            print(f"[macro_filter] calendar fetch failed: {exc}")
            return

        events = []
        for item in raw:
            if item.get("impact") != "High":
                continue
            try:
                dt = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            events.append({
                "title": item.get("title", ""),
                "country": item.get("country", ""),
                "time": dt,
                "impact": item.get("impact", ""),
            })
        self._events = events

    def events(self, hours_ahead: int = 24) -> List[Dict]:
        self.refresh()
        now = datetime.now(timezone.utc)
        out = [e for e in self._events if -1 <= (e["time"] - now).total_seconds() / 3600 <= hours_ahead]
        return sorted(out, key=lambda e: e["time"])

    def in_blackout(self) -> Dict:
        # [CHANGED 2026-08-27, explicit user instruction, following
        # FundedNext's own published News Reward Share Rule] Was a
        # keyword-matched tiered window (60/30/20 min by event type) --
        # both more conservative than FundedNext's own actual +-5 min
        # rule, and it had a real coverage gap (events not matching a
        # tracked keyword, e.g. "Jackson Hole Symposium", got silently
        # ZERO blackout despite being fetched as "High" impact). Every
        # event in self._events already passed refresh()'s impact=="High"
        # filter, so no per-event keyword check is needed anymore -- one
        # flat window (config.NEWS_BLACKOUT_MINUTES) applies to all of
        # them, which fixes that gap as a side effect of the
        # simplification.
        self.refresh()
        now = datetime.now(timezone.utc)
        minutes = NEWS_BLACKOUT_MINUTES
        for e in self._events:
            delta_minutes = abs((e["time"] - now).total_seconds() / 60)
            if delta_minutes <= minutes:
                return {
                    "blocked": True,
                    "event": e["title"],
                    "country": e["country"],
                    "minutes_to_event": round((e["time"] - now).total_seconds() / 60, 1),
                }
        return {"blocked": False, "event": None}


calendar = EconomicCalendar()


def _direction_sync(ticker: str) -> str:
    try:
        df = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=False)
    except Exception:  # noqa: BLE001
        return "unknown"
    if df is None or len(df) < 2:
        return "unknown"
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    closes = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
    try:
        last, prev = float(closes.iloc[-1].item() if hasattr(closes.iloc[-1], "item") else closes.iloc[-1]), \
                     float(closes.iloc[-2].item() if hasattr(closes.iloc[-2], "item") else closes.iloc[-2])
    except Exception:  # noqa: BLE001
        return "unknown"
    return "up" if last > prev else ("down" if last < prev else "flat")


async def macro_snapshot() -> Dict:
    dxy_dir, us10y_dir = await asyncio.gather(
        asyncio.to_thread(_direction_sync, "DX-Y.NYB"),
        asyncio.to_thread(_direction_sync, "^TNX"),
    )
    usd_bias = "unknown"
    if dxy_dir == "up" and us10y_dir == "up":
        usd_bias = "usd_bullish"
    elif dxy_dir == "down" and us10y_dir == "down":
        usd_bias = "usd_bearish"
    elif dxy_dir in ("up", "down") or us10y_dir in ("up", "down"):
        usd_bias = "mixed"

    return {
        "dxy_direction": dxy_dir,
        "us10y_direction": us10y_dir,
        "usd_bias": usd_bias,
        "news_blackout": calendar.in_blackout(),
        "calendar_next_24h": calendar.events(24),
    }
