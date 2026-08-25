"""Performance analytics — broker-source-agnostic (reads whatever broker
object is passed in), with a gross/commission/net split added on top of
the standard trade-stat formulas so commission drag is always visible.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

# Exit reasons that reflect an admin/operational action rather than the
# strategy's own edge -- a manual test close, or (until 2026-08-25) the
# sync-heartbeat's phantom-close bug force-closing paper on a real position
# that hadn't actually finished. Excluding these from a pair's own
# performance ranking matters: stripping them flipped NZDUSD/NZDCAD from
# "worst performers" to roughly break-even, and GBPUSD to net positive, on
# the same day this was discovered -- ranking on unfiltered totals would
# have rotated out pairs based on infrastructure noise, not trading edge.
NON_STRATEGY_EXIT_REASONS: Set[str] = {"manual", "real_sync_close"}


def top_n_symbols(closed: List[Dict], n: int, min_trades: int = 1, metric: str = "pnl_net") -> List[str]:
    """Ranks distinct symbols in `closed` by aggregate net P&L (or gross,
    via metric="pnl_gross") and returns the top `n` symbol names.

    Deliberately takes `closed` as a parameter rather than reading any
    fixed dataset — compute this fresh from whatever trade history you
    actually have (live paper trades once enough have accumulated, or an
    explicit backtest result) at the time you need a top-N scope, rather
    than freezing a ranking from one historical sample into config. See
    config.SYMBOL_SCOPES's docstring for why: per-pair rankings have
    already been observed to swing wildly (best-to-worst) between
    otherwise-similar runs on small per-pair trade counts.

    min_trades filters out symbols with too few trades to rank meaningfully
    (a single lucky/unlucky trade shouldn't crown or bury a pair)."""
    by_symbol = _by_symbol(closed)
    ranked = [
        (sym, row[metric]) for sym, row in by_symbol.items() if row["trades"] >= min_trades
    ]
    ranked.sort(key=lambda kv: kv[1], reverse=True)
    return [sym for sym, _ in ranked[:n]]


def _by_symbol(closed: List[Dict], exclude_reasons: Optional[Set[str]] = None) -> Dict[str, Dict]:
    exclude_reasons = exclude_reasons or set()
    out: Dict[str, Dict] = {}
    for t in closed:
        if t.get("reason") in exclude_reasons:
            continue
        row = out.setdefault(t["symbol"], {
            "trades": 0, "wins": 0, "losses": 0,
            "pnl_gross": 0.0, "commission": 0.0, "pnl_net": 0.0,
            "_gross_profit": 0.0, "_gross_loss": 0.0,
        })
        row["trades"] += 1
        pnl = t.get("pnl", 0.0)
        if pnl > 0:
            row["wins"] += 1
            row["_gross_profit"] += pnl
        elif pnl < 0:
            row["losses"] += 1
            row["_gross_loss"] += -pnl
        row["pnl_gross"] += t.get("pnl_gross", pnl)
        row["commission"] += t.get("commission_paid", 0.0)
        row["pnl_net"] += pnl
    for row in out.values():
        trades = row["trades"]
        row["win_rate"] = round(100 * row["wins"] / trades, 1) if trades else 0.0
        gp, gl = row.pop("_gross_profit"), row.pop("_gross_loss")
        # None (not float('inf')) when there are no losing trades -- see
        # compute_stats()'s own profit_factor comment, same JSON constraint.
        row["profit_factor"] = round(gp / gl, 2) if gl > 0 else (None if gp > 0 else 0.0)
        row["expectancy"] = round(row["pnl_net"] / trades, 2) if trades else 0.0
        for k in ("pnl_gross", "commission", "pnl_net"):
            row[k] = round(row[k], 2)
    return out


_EMPTY_SYMBOL_ROW: Dict = {
    "trades": 0, "wins": 0, "losses": 0, "pnl_gross": 0.0, "commission": 0.0,
    "pnl_net": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
}


def rank_pairs_for_rotation(
    closed: List[Dict],
    all_symbols: List[str],
    min_trades: int = 15,
    keep_top: int = 15,
    exclude_reasons: Optional[Set[str]] = None,
) -> Dict:
    """[ADDED 2026-08-25, explicit user instruction] Ranks pairs for a
    keep-top-N / bench-the-rest rotation, built directly from two findings
    from the same day's review: (1) admin/bug-driven exits must be
    stripped before judging a pair's own edge -- see
    NON_STRATEGY_EXIT_REASONS's own comment for the concrete before/after
    numbers; (2) a pair needs a minimum sample before ranking it means
    anything -- GBPAUD swinging from best to worst pair between two
    otherwise-identical backtest runs is the cautionary example that set
    this gate. Ranks on expectancy (net $/trade), not raw totals or win
    rate alone, since a pair can have a solid win rate and still be net
    negative if average win size is smaller than average loss size (found
    live in AUDJPY/NZDCAD/NZDUSD the same day -- see the early-breakeven
    trigger's role in capping winners).

    Pairs below `min_trades` land in "insufficient_data" regardless of
    which side of zero their expectancy sits on -- not enough evidence to
    keep OR bench yet, including pairs with zero trades at all (e.g.
    EURAUD), which never appear in `closed` and would otherwise vanish
    from the ranking silently."""
    exclude_reasons = exclude_reasons if exclude_reasons is not None else NON_STRATEGY_EXIT_REASONS
    by_symbol = _by_symbol(closed, exclude_reasons=exclude_reasons)
    full = {s: by_symbol.get(s, dict(_EMPTY_SYMBOL_ROW)) for s in all_symbols}

    eligible = {s: r for s, r in full.items() if r["trades"] >= min_trades}
    insufficient = {s: r for s, r in full.items() if r["trades"] < min_trades}

    ranked = sorted(eligible.items(), key=lambda kv: kv[1]["expectancy"], reverse=True)

    return {
        "keep": [{"symbol": s, **r} for s, r in ranked[:keep_top]],
        "bench_candidate": [{"symbol": s, **r} for s, r in ranked[keep_top:]],
        "insufficient_data": [{"symbol": s, **r} for s, r in sorted(insufficient.items())],
        "min_trades_threshold": min_trades,
        "keep_top": keep_top,
        "excluded_reasons": sorted(exclude_reasons),
    }


def compute_stats(broker) -> Dict:
    closed = broker.closed_trades
    total = len(closed)
    wins = [t for t in closed if t.get("pnl", 0.0) > 0]
    losses = [t for t in closed if t.get("pnl", 0.0) < 0]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    win_rate = round(100 * len(wins) / total, 1) if total else 0.0
    avg_win = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    # None (not float('inf')) when there are no losing trades yet — Infinity
    # isn't valid JSON, so an inf here would break the API response; the
    # dashboard renders None as "∞" instead.
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit > 0 else 0.0)
    expectancy = round((gross_profit - gross_loss) / total, 2) if total else 0.0

    drawdown_pct = round(((broker.peak_equity - broker.equity) / broker.peak_equity) * 100.0, 2) if broker.peak_equity else 0.0

    return {
        "total_trades": total,
        "open_trades": len(broker.open_positions),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "pnl_gross": round(sum(t.get("pnl_gross", t.get("pnl", 0.0)) for t in closed), 2),
        "commission_paid": round(sum(t.get("commission_paid", 0.0) for t in closed), 2),
        "pnl_net": round(sum(t.get("pnl", 0.0) for t in closed), 2),
        "equity": round(broker.equity, 2),
        "balance": round(broker.balance, 2),
        "peak_equity": round(broker.peak_equity, 2),
        "drawdown_pct": drawdown_pct,
        "by_symbol": _by_symbol(closed),
    }
