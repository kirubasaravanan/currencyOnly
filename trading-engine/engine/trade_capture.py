"""Per-trade JSON capture for the AI research pipeline — writes a snapshot
of each closed trade to the shared ForexTradeAnalysis repository the
instant it closes, the moment close_trade() runs.

[ADD 2026-09-08] Mirrors the Forex (gold) app's own engine/trade_capture.py
(APP_TAG="forex") — same repo, same "trades/YYYY-MM/YYYY-MM-DD/" tree,
both apps writing side by side so this VPS has one single place with every
closed trade from both engines. Two things this shares with the Forex
app's version, both learned from auditing its already-running capture
before building this one:
  1. Filename is "<app>_<id>_<closed_epoch_ms>.json", not just "<id>.json".
     A bare "<id>.json" collides whenever the broker's own trade_counter
     restarts from 1 (a reset() call) — confirmed happening on the Forex
     app's old-VPS data (a reset around 2026-08-27 produced ids 1-10 that
     already existed from 2026-08-03; they only avoided overwriting each
     other by landing in different day-folders, not by design). With two
     apps now sharing one repo, a same-day collision is far more likely
     without a properly unique name — the app tag + closed-time-in-ms
     makes one impossible.
  2. Each captured record carries "source_app": "currencyonly" so anything
     reading the shared trades/ tree can tell which engine produced a
     given trade (useful when both apps trade the same symbol).

Deliberately just a local file write here — no git commit/push per trade.
Committing the day's captures is a separate, coarser-grained scheduled
job (see storage/commit_trade_capture.ps1), same pattern as the Forex app.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

APP_TAG = "currencyonly"

IST_OFFSET = timedelta(hours=5, minutes=30)

REPO_PATH = os.getenv("TRADE_ANALYSIS_REPO_PATH") or os.getenv(
    "FOREX_TRADE_ANALYSIS_REPO_PATH", r"C:\ForexTradeAnalysis"
)


def _ist_date_parts(closed_at_raw: str) -> tuple[str, str]:
    """Returns (YYYY-MM, YYYY-MM-DD) in IST for a trade's closed_at."""
    dt = datetime.fromisoformat(closed_at_raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist = dt.astimezone(timezone.utc) + IST_OFFSET
    return ist.strftime("%Y-%m"), ist.strftime("%Y-%m-%d")


def capture_trade(trade: Dict) -> bool:
    """Writes one JSON file for this closed trade into
    <REPO_PATH>/trades/<YYYY-MM>/<YYYY-MM-DD>/<app>_<id>_<closed_epoch_ms>.json.
    Never raises — a capture failure (e.g. repo path missing on this
    machine) must not break close_trade() or anything downstream of it;
    logs and returns False instead."""
    try:
        closed_at = trade.get("closed_at")
        trade_id = trade.get("id")
        if not closed_at or trade_id is None:
            return False
        month_dir, day_str = _ist_date_parts(closed_at)
        target_dir = os.path.join(REPO_PATH, "trades", month_dir, day_str)
        os.makedirs(target_dir, exist_ok=True)
        dt = datetime.fromisoformat(closed_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        closed_epoch_ms = int(dt.timestamp() * 1000)
        target_path = os.path.join(target_dir, f"{APP_TAG}_{trade_id}_{closed_epoch_ms}.json")
        record = dict(trade)
        record["source_app"] = APP_TAG
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, default=str)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[trade_capture] failed to capture trade {trade.get('id')}: {exc}")
        return False
