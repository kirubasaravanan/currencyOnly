"""OANDA-primary, Yahoo-Finance-fallback candle fetcher.

Data only. This module (and this entire app) never places, modifies, or
closes an order anywhere — it only ever calls OANDA's read-only
/v3/instruments/{instrument}/candles endpoint. There is deliberately no
OANDA_ACCOUNT_ID anywhere in this repo (order endpoints need one), and no
import of any order-placement client.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Dict, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

from config import (
    OANDA_SYMBOL_MAP,
    OANDA_GRANULARITY_MAP,
    YAHOO_SYMBOL_MAP,
    YF_INTERVAL_MAP,
    YF_PERIOD_MAP,
)

load_dotenv()

OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "")
# Practice/demo endpoint only, hardcoded deliberately — never the live one.
OANDA_BASE_URL = "https://api-fxpractice.oanda.com"

CACHE_TTL_SECONDS = 60


class MarketData:
    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
        self._source: Dict[str, str] = {}

    async def fetch(self, symbol: str, timeframe: str, bars: int = 500) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            ts, df = cached
            # A cache hit only counts if it actually has enough bars for
            # this call — otherwise a later request asking for more history
            # than an earlier one would silently get truncated data.
            if time.time() - ts < CACHE_TTL_SECONDS and len(df) >= bars:
                return df.tail(bars)

        df = await self._fetch_oanda(symbol, timeframe, bars)
        source = "oanda"
        if df is None or df.empty:
            df = await asyncio.to_thread(self._fetch_yahoo_sync, symbol, timeframe, bars)
            source = "yahoo"
        if df is None or df.empty:
            df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            source = "unavailable"

        self._source[cache_key] = source
        self._cache[cache_key] = (time.time(), df)
        return df.tail(bars)

    async def fetch_multi(self, symbol: str, timeframes) -> Dict[str, pd.DataFrame]:
        return {tf: await self.fetch(symbol, tf) for tf in timeframes}

    async def _fetch_oanda(self, symbol: str, timeframe: str, bars: int) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._fetch_oanda_sync, symbol, timeframe, bars)

    def _fetch_oanda_sync(self, symbol: str, timeframe: str, bars: int = 500) -> Optional[pd.DataFrame]:
        if not OANDA_API_TOKEN:
            return None
        instrument = OANDA_SYMBOL_MAP.get(symbol)
        granularity = OANDA_GRANULARITY_MAP.get(timeframe)
        if not instrument or not granularity:
            return None
        count = min(int(bars), 5000)  # OANDA's own hard limit per request
        url = f"{OANDA_BASE_URL}/v3/instruments/{instrument}/candles"
        params = {"granularity": granularity, "count": count, "price": "M"}
        headers = {"Authorization": f"Bearer {OANDA_API_TOKEN}"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[market_data] OANDA fetch failed for {symbol}/{timeframe}: {exc}")
            return None

        rows = []
        for c in data.get("candles", []):
            if not c.get("complete"):  # drop the in-progress candle
                continue
            mid = c["mid"]
            rows.append({
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c.get("volume", 0)),
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.set_index("time").sort_index()

    def _fetch_yahoo_sync(self, symbol: str, timeframe: str, bars: int = 500) -> Optional[pd.DataFrame]:
        ticker = YAHOO_SYMBOL_MAP.get(symbol)
        interval = YF_INTERVAL_MAP.get(timeframe)
        period = YF_PERIOD_MAP.get(timeframe)
        if not ticker or not interval or not period:
            return None
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[market_data] Yahoo fetch failed for {symbol}/{timeframe}: {exc}")
            return None
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        if timeframe == "4h":  # yfinance has no native 4h — resample from 1h
            df = df.resample("4h").agg({
                "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
            }).dropna()
        return df[["open", "high", "low", "close", "volume"]]

    def latest_price(self, symbol: str, timeframe: str = "15m") -> Optional[float]:
        cached = self._cache.get(f"{symbol}_{timeframe}")
        if cached is None or cached[1].empty:
            return None
        return float(cached[1]["close"].iloc[-1])

    def get_source(self, symbol: str, timeframe: str) -> str:
        return self._source.get(f"{symbol}_{timeframe}", "unknown")

    def clear_cache(self) -> None:
        self._cache.clear()
        self._source.clear()


market_data = MarketData()
