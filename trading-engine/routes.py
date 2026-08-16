"""REST endpoints. Trimmed to what an FX-only, single-account, paper-only
app actually needs — no multi-account/gold-comparison endpoints from the
existing Forex/Forex app, none of which apply here.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
from config import PAIRS, PAIR_CALIBRATION
from data.market_data import market_data
from engine import orchestrator
from engine.analytics import compute_stats
from engine.backtester import run_backtest
from engine.entry import entry_signal
from engine.macro_filter import calendar, macro_snapshot
from engine.mtf_trend import mtf_alignment
from engine.paper_broker import broker
from engine.session_dominance import dominance_score
from indicators.indicators import compute_all, market_structure

_backtest_runs: Dict[str, Dict] = {}
_macro_cache: Dict[str, object] = {"ts": 0.0, "data": None}
MACRO_CACHE_TTL_SECONDS = 60


def _calibration_dict(symbol: str) -> Dict:
    c = PAIR_CALIBRATION[symbol]
    return {
        "session_windows_ist": c.session_windows_ist,
        "stop_mult": c.stop_mult,
        "tp_mult": c.tp_mult,
        "tp2_mult": c.tp2_mult,
        "para_thresh": c.para_thresh,
        "sweep_lb": c.sweep_lb,
        "sweep_mem": c.sweep_mem,
        "mss_lb": c.mss_lb,
        "max_sl_pips": c.max_sl_pips,
        "min_sl_pips": c.min_sl_pips,
        "conf_boost": c.conf_boost,
        "use_trend_filter": c.use_trend_filter,
        "activation_usd": c.activation_usd,
        "activation_pct": c.activation_pct,
        "trail_lock_pct": c.trail_lock_pct,
        "confidence_threshold": round(config.state.confidence_threshold + c.conf_boost, 4),
    }


class ThresholdRequest(BaseModel):
    threshold: float


class BacktestRequest(BaseModel):
    symbols: Optional[List[str]] = None
    days: int = 30


async def _run_backtest_task(run_id: str, symbols: Optional[List[str]], days: int) -> None:
    entry = _backtest_runs[run_id]
    try:
        def progress_cb(pct: float) -> None:
            entry["progress"] = round(pct, 3)

        result = await run_backtest(symbols=symbols, days=days, progress_cb=progress_cb)
        entry["status"] = "error" if "error" in result else "done"
        entry["result"] = result
        entry["progress"] = 1.0
    except Exception as exc:  # noqa: BLE001
        entry["status"] = "error"
        entry["error"] = str(exc)


def register_routes(app: FastAPI) -> None:
    @app.get("/snapshot")
    async def snapshot():
        return {
            "engine": {
                "running": config.state.running,
                "started_at": config.state.started_at,
                "last_scan_at": config.state.last_scan_at,
                "scan_count": config.state.scan_count,
                "last_error": orchestrator.last_error,
            },
            "pairs": PAIRS,
            "calibration": {s: _calibration_dict(s) for s in PAIRS},
            "signals": orchestrator.last_signals,
            "open_trades": broker.open_positions,
            "closed_trades": broker.closed_trades[-50:],
            "stats": compute_stats(broker),
            "threshold": config.state.confidence_threshold,
        }

    @app.get("/stats")
    async def stats():
        return compute_stats(broker)

    @app.get("/trades")
    async def trades():
        return {"open": broker.open_positions, "closed": broker.closed_trades[-100:]}

    @app.post("/trades/{trade_id}/close")
    async def close_trade(trade_id: int):
        trade = next((t for t in broker.open_positions if t["id"] == trade_id), None)
        if trade is None:
            raise HTTPException(status_code=404, detail="trade not found")
        price = market_data.latest_price(trade["symbol"], "15m") or trade["entry_price"]
        closed = broker.close_trade(trade_id, price, "manual")
        return closed

    @app.post("/reset")
    async def reset():
        broker.reset()
        return {"status": "reset"}

    @app.get("/signals")
    async def signals():
        return orchestrator.last_signals

    @app.get("/pairs")
    async def pairs():
        return {"pairs": PAIRS, "calibration": {s: _calibration_dict(s) for s in PAIRS}}

    @app.get("/pairs/{symbol}/analysis")
    async def pair_analysis(symbol: str):
        symbol = symbol.upper()
        if symbol not in PAIRS:
            raise HTTPException(status_code=404, detail="unknown symbol")
        frames = await market_data.fetch_multi(symbol, config.MTF_TIMEFRAMES)
        df15 = frames.get("15m")
        if df15 is None or df15.empty:
            raise HTTPException(status_code=503, detail="no data available")
        computed = compute_all(df15)
        last = computed.iloc[-1]
        return {
            "symbol": symbol,
            "close": float(last["close"]),
            "rsi14": round(float(last["rsi14"]), 2),
            "adx14": round(float(last["adx14"]), 2),
            "atr14": round(float(last["atr14"]), 6),
            "chop14": round(float(last["chop14"]), 2),
            "supertrend_direction": int(last["supertrend_direction"]),
            "market_structure": market_structure(df15),
            "mtf_alignment": mtf_alignment(frames),
            "session_dominance": dominance_score(symbol),
            "calibration": _calibration_dict(symbol),
        }

    @app.get("/macro")
    async def macro():
        now = time.time()
        if _macro_cache["data"] is None or now - _macro_cache["ts"] > MACRO_CACHE_TTL_SECONDS:
            _macro_cache["data"] = await macro_snapshot()
            _macro_cache["ts"] = now
        return _macro_cache["data"]

    @app.get("/calendar")
    async def calendar_events(hours: int = 24):
        return {"events": calendar.events(hours)}

    @app.post("/threshold")
    async def set_threshold(req: ThresholdRequest):
        config.state.confidence_threshold = max(0.0, min(1.0, req.threshold))
        return {"threshold": config.state.confidence_threshold}

    @app.post("/backtest/run")
    async def backtest_run(req: BacktestRequest):
        run_id = str(uuid.uuid4())[:8]
        _backtest_runs[run_id] = {
            "id": run_id,
            "status": "running",
            "progress": 0.0,
            "symbols": req.symbols or PAIRS,
            "days": req.days,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        asyncio.create_task(_run_backtest_task(run_id, req.symbols, req.days))
        return _backtest_runs[run_id]

    @app.get("/backtest/status/{run_id}")
    async def backtest_status(run_id: str):
        entry = _backtest_runs.get(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {k: v for k, v in entry.items() if k != "result"}

    @app.get("/backtest/results")
    async def backtest_results():
        return [
            {k: v for k, v in e.items() if k != "result"}
            for e in sorted(_backtest_runs.values(), key=lambda e: e["started_at"], reverse=True)
        ]

    @app.get("/backtest/results/{run_id}")
    async def backtest_result(run_id: str):
        entry = _backtest_runs.get(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="run not found")
        if entry["status"] != "done":
            return entry
        return entry["result"]
