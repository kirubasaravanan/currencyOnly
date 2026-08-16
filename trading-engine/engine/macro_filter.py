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

from config import EVENT_IMPACT_TIERS, HIGH_IMPACT_EVENTS, NEWS_BLACKOUT_MINUTES

FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_CACHE_TTL_SECONDS = 600


def _blackout_minutes_for(title: str) -> int:
    lowered = title.lower()
    for _, keywords, minutes in EVENT_IMPACT_TIERS:
        if any(k.lower() in lowered for k in keywords):
            return minutes
    if any(k.lower() in lowered for k in HIGH_IMPACT_EVENTS):
        return NEWS_BLACKOUT_MINUTES
    return 0


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
        self.refresh()
        now = datetime.now(timezone.utc)
        for e in self._events:
            minutes = _blackout_minutes_for(e["title"])
            if minutes <= 0:
                continue
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
