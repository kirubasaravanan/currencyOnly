"""Discord webhook alerts: entry, exit, session summaries, EOD summary.

Live-only — orchestrator.py wires these in; the backtester never imports
this module, so a historical replay can never spam Discord with months of
fake alerts. Every send is fire-and-forget (best-effort — a slow or failed
webhook never blocks or crashes the scan loop).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

GREEN = 0x26A875
RED = 0xE5484D
BLUE = 0x4F8CFF
AMBER = 0xD4A72C


def _send_sync(payload: Dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
    except Exception as exc:  # noqa: BLE001
        print(f"[discord_alerts] send failed: {exc}")


async def _send_embed(embed: Dict) -> None:
    embed.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    await asyncio.to_thread(_send_sync, {"embeds": [embed]})


def _fmt_price(v: Optional[float], decimals: int = 5) -> str:
    if v is None:
        return "-"
    try:
        return f"{v:.{decimals}f}"
    except Exception:  # noqa: BLE001
        return str(v)


IST_OFFSET = timedelta(hours=5, minutes=30)


def _fmt_ist(iso_ts: Optional[str]) -> str:
    """opened_at is stored as UTC isoformat; render in IST (this app's
    timezone convention throughout, e.g. orchestrator.py session/EOD
    rollovers) so an entry and its later exit alert can be matched up
    without doing UTC->IST math by hand."""
    if not iso_ts:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_ts) + IST_OFFSET
        return dt.strftime("%H:%M:%S IST")
    except Exception:  # noqa: BLE001
        return str(iso_ts)


async def alert_trade_opened(trade: Dict) -> None:
    is_long = trade.get("side") == "BULLISH"
    embed = {
        "title": f"{'🟢' if is_long else '🔴'} {trade['symbol']} {trade.get('side')} — ENTRY",
        "color": GREEN if is_long else RED,
        "fields": [
            {"name": "Entry", "value": _fmt_price(trade.get("entry_price")), "inline": True},
            {"name": "SL", "value": _fmt_price(trade.get("sl_price")), "inline": True},
            {"name": "TP1", "value": _fmt_price(trade.get("tp_price")), "inline": True},
            {"name": "TP2", "value": _fmt_price(trade.get("tp2_price")), "inline": True},
            {"name": "Lots", "value": str(trade.get("lots")), "inline": True},
            {"name": "Confidence", "value": f"{trade.get('confidence', 0) * 100:.0f}%", "inline": True},
            {"name": "Session", "value": str(trade.get("session", "-")), "inline": True},
        ],
    }
    await _send_embed(embed)


async def alert_trade_closed(trade: Dict) -> None:
    net = trade.get("pnl", 0.0)
    win = net > 0
    embed = {
        "title": f"{'✅' if win else '❌'} {trade['symbol']} CLOSED — {trade.get('reason', '')}",
        "color": GREEN if win else RED,
        "fields": [
            {"name": "Entry", "value": _fmt_price(trade.get("entry_price")), "inline": True},
            {"name": "Entry Time", "value": _fmt_ist(trade.get("opened_at")), "inline": True},
            {"name": "Exit", "value": _fmt_price(trade.get("exit_price")), "inline": True},
            {"name": "Gross", "value": f"${trade.get('pnl_gross', net):.2f}", "inline": True},
            {"name": "Commission", "value": f"${trade.get('commission_paid', 0):.2f}", "inline": True},
            {"name": "Net", "value": f"${net:.2f}", "inline": True},
        ],
    }
    await _send_embed(embed)


async def alert_partial_close(trade: Dict, relayed: bool) -> None:
    """[ADD 2026-08-17, explicit user instruction: "update me if... partial
    close is happening or not"] Distinct from alert_trade_closed -- fires
    the moment paper's 50%-at-TP1 partial takes, not at the trade's
    eventual full close, so the pct=0.5 fix's success/failure is visible
    trade-by-trade rather than only inferable from the log or a silent
    TradeSgnl email (see tradesgnl_relay.py's 2026-08-17 fix note)."""
    status = "✅ relayed to real MT5" if relayed else "❌ relay FAILED — real position still at original size"
    embed = {
        "title": f"📊 {trade['symbol']} PARTIAL CLOSE (50% @ TP1)",
        "color": GREEN if relayed else RED,
        "fields": [
            {"name": "Lots closed", "value": f"{trade.get('original_lots', 0) - trade.get('lots', 0):.2f}", "inline": True},
            {"name": "Remaining lots", "value": f"{trade.get('lots', 0):.2f}", "inline": True},
            {"name": "Partial P&L (net)", "value": f"${trade.get('partial_pnl_net', 0):.2f}", "inline": True},
            {"name": "Real MT5 relay", "value": status, "inline": False},
        ],
    }
    await _send_embed(embed)


async def alert_relay_failure(symbol: str, kind: str, command: str) -> None:
    """[ADD 2026-08-17, found live] tradesgnl_relay.py's _send_sync() only
    ever printed a failed webhook response to the log file -- nothing
    surfaced to Discord. A GBPJPY partial-close silently failed (TradeSgnl
    rejected an invalid command) and went unnoticed for ~90 minutes, found
    only because the user happened to check a TradeSgnl email directly.
    Every relay call now routes its failure here instead of staying log-only."""
    embed = {
        "title": f"🔴 Real MT5 relay send failed — {symbol} ({kind})",
        "color": RED,
        "fields": [{"name": "Command sent", "value": command, "inline": False}],
    }
    await _send_embed(embed)


async def alert_sync_heartbeat(result: Dict) -> None:
    """Only called when trade_sync_heartbeat.run_heartbeat() found something
    worth surfacing (see orchestrator.py's dispatch) -- a clean check never
    posts anything, so this channel doesn't get a message every 5 minutes."""
    phantoms = result.get("direction1_confirmed_phantoms", [])
    still_open = result.get("direction2_still_open_on_real", [])
    errors = result.get("errors", [])

    fields = []
    for p in phantoms:
        fields.append({
            "name": f"🟠 {p['symbol']} closed on real, still open in paper",
            "value": f"real P&L ${p['real_pnl']:.2f} @ {p['real_close_time_utc']} (paper still tracking it live)",
            "inline": False,
        })
    for s in still_open:
        fields.append({
            "name": f"🔴 {s['symbol']} closed in paper, still open on real (ticket {s['real_ticket']})",
            "value": f"paper closed {s['paper_closed_at']} ({s['paper_close_reason']}) -- real position live P&L ${s['real_profit']:.2f}, {s['real_volume']} lots",
            "inline": False,
        })
    for e in errors:
        fields.append({"name": "⚠️ heartbeat check error", "value": e, "inline": False})

    if not fields:
        return

    embed = {
        "title": "🔁 Trade sync desync detected",
        "color": RED,
        "fields": fields,
    }
    await _send_embed(embed)


def _aggregate(trades: List[Dict]) -> Dict:
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    losses = len(trades) - wins
    return {
        "wins": wins,
        "losses": losses,
        "gross": sum(t.get("pnl_gross", t.get("pnl", 0)) for t in trades),
        "commission": sum(t.get("commission_paid", 0) for t in trades),
        "net": sum(t.get("pnl", 0) for t in trades),
    }


async def alert_session_summary(session_name: str, trades: List[Dict], scope_label: str = "ALL 17 PAIRS") -> None:
    """scope_label distinguishes which symbol subset this summary covers —
    orchestrator.py sends this once for MAJORS and once for ALL 17 PAIRS
    per session/EOD, per explicit user request, so majors-only performance
    is visible separately from the full portfolio while deciding which
    crosses (if any) to add on top of a majors-only core."""
    if not trades:
        return
    agg = _aggregate(trades)
    lines = "\n".join(f"{t['symbol']}: ${t.get('pnl', 0):.2f} ({t.get('reason', '')})" for t in trades)
    embed = {
        "title": f"📊 {session_name.upper()} SESSION SUMMARY — {scope_label}",
        "color": BLUE,
        "description": lines[:4000],
        "fields": [
            {"name": "Trades", "value": f"{len(trades)} ({agg['wins']}W/{agg['losses']}L)", "inline": True},
            {"name": "Gross", "value": f"${agg['gross']:.2f}", "inline": True},
            {"name": "Commission", "value": f"${agg['commission']:.2f}", "inline": True},
            {"name": "Net", "value": f"${agg['net']:.2f}", "inline": True},
        ],
    }
    await _send_embed(embed)


async def alert_eod_summary(date_str: str, trades: List[Dict], equity: float, drawdown_pct: float,
                             scope_label: str = "ALL 17 PAIRS") -> None:
    agg = _aggregate(trades)
    lines = "\n".join(f"{t['symbol']}: ${t.get('pnl', 0):.2f} ({t.get('reason', '')})" for t in trades)
    embed = {
        "title": f"🌙 END OF DAY SUMMARY — {date_str} — {scope_label}",
        "color": AMBER,
        "description": lines[:4000],
        "fields": [
            {"name": "Trades", "value": f"{len(trades)} ({agg['wins']}W/{agg['losses']}L)", "inline": True},
            {"name": "Gross", "value": f"${agg['gross']:.2f}", "inline": True},
            {"name": "Commission", "value": f"${agg['commission']:.2f}", "inline": True},
            {"name": "Net", "value": f"${agg['net']:.2f}", "inline": True},
            {"name": "Equity", "value": f"${equity:.2f}", "inline": True},
            {"name": "Drawdown", "value": f"{drawdown_pct:.2f}%", "inline": True},
        ],
    }
    await _send_embed(embed)


async def alert_engine_event(title: str, description: str = "", color: int = BLUE) -> None:
    await _send_embed({"title": title, "description": description, "color": color})
