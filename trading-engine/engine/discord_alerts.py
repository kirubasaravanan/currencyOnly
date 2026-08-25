"""Discord webhook alerts: entry, exit, session summaries, EOD summary.

Live-only — orchestrator.py wires these in; the backtester never imports
this module, so a historical replay can never spam Discord with months of
fake alerts. Every send is fire-and-forget (best-effort — a slow or failed
webhook never blocks or crashes the scan loop).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

import config

load_dotenv()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

GREEN = 0x26A875
RED = 0xE5484D
BLUE = 0x4F8CFF
AMBER = 0xD4A72C


def _send_sync(payload: Dict) -> None:
    """[FIX 2026-08-24, found live] Previously never checked the response
    status -- requests.post() doesn't raise on a non-2xx, so a rejected or
    rate-limited webhook (Discord's per-webhook limit is easy to hit when
    two alerts fire in the same scan cycle, e.g. alert_trade_closed() and
    alert_sync_heartbeat() both firing for the same real_sync_close) failed
    completely silently -- no log line anywhere, no way to tell after the
    fact whether a specific message ever went out. Confirmed missing this
    way: an AUDCAD trade closed via real_sync_close never showed its
    "CLOSED" embed, and there was nothing in the log to explain why.

    Now logs any non-2xx response, and retries once on 429 specifically
    (the likely culprit for the AUDCAD case) after honoring Discord's own
    Retry-After header -- a single bounded retry, not a queue, since this
    is a best-effort alert channel, not a delivery-guaranteed one."""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
    except Exception as exc:  # noqa: BLE001
        print(f"[discord_alerts] send failed: {exc}")
        return

    if r.status_code in (200, 204):
        return

    if r.status_code == 429:
        retry_after = 1.0
        try:
            retry_after = float(r.json().get("retry_after", 1.0))
        except Exception:  # noqa: BLE001
            pass
        print(f"[discord_alerts] rate-limited (429), retrying once after {retry_after:.2f}s")
        time.sleep(retry_after)
        try:
            r2 = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=8)
            if r2.status_code not in (200, 204):
                print(f"[discord_alerts] retry also failed ({r2.status_code}): {r2.text[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[discord_alerts] retry send failed: {exc}")
        return

    print(f"[discord_alerts] webhook returned {r.status_code}: {r.text[:200]}")


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


async def alert_relay_failure(symbol: str, kind: str, command: str, relay: str = "tradesgnl") -> None:
    """[ADD 2026-08-17, found live] tradesgnl_relay.py's _send_sync() only
    ever printed a failed webhook response to the log file -- nothing
    surfaced to Discord. A GBPJPY partial-close silently failed (TradeSgnl
    rejected an invalid command) and went unnoticed for ~90 minutes, found
    only because the user happened to check a TradeSgnl email directly.
    Every relay call now routes its failure here instead of staying log-only.

    [ADD 2026-08-19] `relay` distinguishes which real connection failed now
    that pineconnector_relay.py runs alongside tradesgnl_relay.py as a
    second, independent real connection -- defaults to "tradesgnl" so the
    original call site (which never passed this) keeps working unchanged."""
    embed = {
        "title": f"🔴 Real MT5 relay send failed — {relay} — {symbol} ({kind})",
        "color": RED,
        "fields": [{"name": "Command sent", "value": command, "inline": False}],
    }
    await _send_embed(embed)


async def alert_sync_heartbeat(results_by_account: Dict) -> None:
    """Only called when trade_sync_heartbeat.run_heartbeat() found something
    worth surfacing (see orchestrator.py's dispatch) -- a clean check never
    posts anything, so this channel doesn't get a message every 5 minutes.

    [UPDATED 2026-08-21] results_by_account is now {"TradeSgnl": {...},
    "FundedNext": {...}} -- one heartbeat result dict per account. Also
    reflects that the heartbeat now ACTS on confirmed desyncs instead of
    only alerting, so the wording below describes what happened, not just
    what was found -- see trade_sync_heartbeat.py's module docstring."""
    fields = []
    for account, result in results_by_account.items():
        paused = result.get("actions_paused", False)
        if paused:
            for p in result.get("confirmed_phantoms", []):
                fields.append({
                    "name": f"⏸️ [{account}] {p['symbol']} closed on real -- paper NOT auto-closed (actions paused)",
                    "value": f"real P&L ${p['real_pnl']:.2f} @ {p['real_close_time_utc']} -- would have force-closed paper to match; review manually while HEARTBEAT_AUTO_ACTIONS_ENABLED=False",
                    "inline": False,
                })
            for s in result.get("still_open_on_real", []):
                fields.append({
                    "name": f"⏸️ [{account}] {s['symbol']} closed in paper, real still open -- NOT auto-closed (actions paused)",
                    "value": f"paper closed {s['paper_closed_at']} ({s['paper_close_reason']}) -- real ticket {s['real_ticket']} still live P&L ${s['real_profit']:.2f}, {s['real_volume']} lots; would have sent a close, review manually while HEARTBEAT_AUTO_ACTIONS_ENABLED=False",
                    "inline": False,
                })
        for u in result.get("uncertain", []):
            fields.append({
                "name": f"🟡 [{account}] {u['symbol']} may be desynced (paper open, can't confirm real side)",
                "value": f"unresolved for {u['consecutive_checks']} consecutive checks (~{u['consecutive_checks']*5}min) -- reason: {u['reason']}, paper P&L ${u.get('internal_pnl', 0):.2f}",
                "inline": False,
            })
        for p in result.get("direction1_closed_paper", []):
            fields.append({
                "name": f"🟠 [{account}] {p['symbol']} closed on real -- paper force-closed to match",
                "value": f"real P&L ${p['real_pnl']:.2f} @ {p['real_close_time_utc']} (paper was still tracking it live before this)",
                "inline": False,
            })
        for s in result.get("direction2_closed_real", []):
            fields.append({
                "name": f"🔴 [{account}] {s['symbol']} closed in paper -- real position closed to match (confirmed)",
                "value": f"paper closed {s['paper_closed_at']} ({s['paper_close_reason']}) -- real ticket {s['real_ticket']} was live P&L ${s['real_profit']:.2f}, {s['real_volume']} lots, now verified gone",
                "inline": False,
            })
        for s in result.get("direction2_close_unverified", []):
            fields.append({
                "name": f"🔴🔺 [{account}] {s['symbol']} closed in paper, real close attempted but UNVERIFIED (ticket {s['real_ticket']})",
                "value": f"paper closed {s['paper_closed_at']} ({s['paper_close_reason']}) -- sent a close for real P&L ${s['real_profit']:.2f}, {s['real_volume']} lots, but couldn't confirm it actually closed. Check the account directly.",
                "inline": False,
            })
        for m in result.get("lot_mismatches", []):
            fields.append({
                "name": f"🟣 [{account}] {m['symbol']} lot-size mismatch (partial-close desync, not auto-corrected)",
                "value": f"paper shows {m['paper_lots']} lots, real ticket {m['real_ticket']} shows {m['real_volume']} lots -- one side's partial-close likely didn't relay",
                "inline": False,
            })
        for e in result.get("errors", []):
            fields.append({"name": f"⚠️ [{account}] heartbeat check error", "value": e, "inline": False})

    if not fields:
        return

    embed = {
        "title": "🔁 Trade sync desync detected",
        "color": RED,
        "fields": fields,
    }
    await _send_embed(embed)


async def alert_daily_giveback_triggered(peak: float, current: float, closed_symbols: List[str]) -> None:
    """[ADD 2026-08-18, explicit user instruction, calibrated on a real
    60-day backtest] Fires once, the moment orchestrator's daily give-back
    breaker actually trips -- see config.DAILY_GIVEBACK_MIN_PEAK/_PCT for
    the threshold and its evidence basis."""
    giveback = peak - current
    giveback_pct = (giveback / peak * 100) if peak > 0 else 0
    embed = {
        "title": "🛑 DAILY GIVE-BACK LIMIT HIT — closing all positions, entries paused for today",
        "color": RED,
        "fields": [
            {"name": "Today's peak P&L", "value": f"${peak:.2f}", "inline": True},
            {"name": "P&L when triggered", "value": f"${current:.2f}", "inline": True},
            {"name": "Given back", "value": f"${giveback:.2f} ({giveback_pct:.1f}%)", "inline": True},
            {"name": "Positions force-closed", "value": ", ".join(closed_symbols) if closed_symbols else "none were open", "inline": False},
        ],
        "description": f"Threshold: {config.DAILY_GIVEBACK_PCT}% given back from a peak of at least ${config.DAILY_GIVEBACK_MIN_PEAK:.0f}. New entries resume next trading day.",
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


_RISK_REASON_LABELS = {
    "max_open_trades": f"Max open trades reached ({config.MAX_OPEN_TRADES})",
    "max_drawdown": f"Max drawdown reached ({config.MAX_DRAWDOWN_PCT}%)",
    "daily_loss_limit": f"Daily loss limit reached ({config.DAILY_LOSS_LIMIT}% of equity)",
    "weekly_loss_limit": f"Weekly loss limit reached ({config.WEEKLY_LOSS_LIMIT}% of equity)",
}


async def alert_risk_limit_breached(reason: str, equity: float, peak_equity: float, drawdown_pct: float) -> None:
    """[ADD 2026-08-18, explicit user instruction] can_open_new_trade()'s
    block reason was previously discarded silently every scan -- no log,
    no Discord alert -- same gap already found and fixed in the sister
    Forex app the day before. New entries would simply stop with zero
    visible explanation. orchestrator._check_risk_limit_alert() calls this
    once on the transition INTO a blocked state (not every scan while it
    stays blocked)."""
    label = _RISK_REASON_LABELS.get(reason, reason)
    embed = {
        "title": f"🛑 New entries paused — {label}",
        "color": RED,
        "fields": [
            {"name": "Equity", "value": f"${equity:.2f}", "inline": True},
            {"name": "Peak equity", "value": f"${peak_equity:.2f}", "inline": True},
            {"name": "Drawdown", "value": f"{drawdown_pct:.2f}%", "inline": True},
        ],
        "description": "Existing open positions are unaffected -- this only blocks new entries until the limit clears.",
    }
    await _send_embed(embed)


async def alert_sod_status(date_str: str, equity: float, peak_equity: float, drawdown_pct: float) -> None:
    """[ADD 2026-08-18, explicit user instruction: "a start of the day
    message kind of"] Proactive daily status ping, independent of whether
    anything is actually breached -- fires once, shortly before the
    earliest pair's session opens (05:25 IST), so risk-limit standing is
    visible every day rather than only reactively when something trips."""
    embed = {
        "title": f"☀️ START OF DAY — {date_str}",
        "color": BLUE,
        "fields": [
            {"name": "Equity", "value": f"${equity:.2f}", "inline": True},
            {"name": "Peak equity", "value": f"${peak_equity:.2f}", "inline": True},
            {"name": "Drawdown", "value": f"{drawdown_pct:.2f}%", "inline": True},
            {"name": "Daily loss limit", "value": f"{config.DAILY_LOSS_LIMIT}% of equity", "inline": True},
            {"name": "Weekly loss limit", "value": f"{config.WEEKLY_LOSS_LIMIT}% of equity", "inline": True},
            {"name": "Max drawdown limit", "value": f"{config.MAX_DRAWDOWN_PCT}%", "inline": True},
        ],
    }
    await _send_embed(embed)
