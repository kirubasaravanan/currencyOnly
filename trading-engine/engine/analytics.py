"""Performance analytics — broker-source-agnostic (reads whatever broker
object is passed in), with a gross/commission/net split added on top of
the standard trade-stat formulas so commission drag is always visible.
"""

from __future__ import annotations

from typing import Dict, List


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


def _by_symbol(closed: List[Dict]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for t in closed:
        row = out.setdefault(t["symbol"], {
            "trades": 0, "wins": 0, "losses": 0,
            "pnl_gross": 0.0, "commission": 0.0, "pnl_net": 0.0,
        })
        row["trades"] += 1
        pnl = t.get("pnl", 0.0)
        if pnl > 0:
            row["wins"] += 1
        elif pnl < 0:
            row["losses"] += 1
        row["pnl_gross"] += t.get("pnl_gross", pnl)
        row["commission"] += t.get("commission_paid", 0.0)
        row["pnl_net"] += pnl
    for row in out.values():
        row["win_rate"] = round(100 * row["wins"] / row["trades"], 1) if row["trades"] else 0.0
        for k in ("pnl_gross", "commission", "pnl_net"):
            row[k] = round(row[k], 2)
    return out


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
