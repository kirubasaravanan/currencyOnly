"use client";

import { useState } from "react";
import { Card, CardHeader, Badge } from "@/components/ui";
import { api, type BacktestResult } from "@/lib/trading-api";
import { fmtUsd, fmtPct } from "@/lib/utils";

export function BacktestPanel({ pairs }: { pairs: string[] }) {
  const [selected, setSelected] = useState<string[]>(pairs);
  const [days, setDays] = useState(60);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggle(p: string) {
    setSelected((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  }

  async function run() {
    setError(null);
    setResult(null);
    try {
      const started = await api.runBacktest(selected.length ? selected : null, days);
      setRunId(started.id);
      setStatus(started.status);
      poll(started.id);
    } catch (e) {
      setError(String(e));
    }
  }

  function poll(id: string) {
    const interval = setInterval(async () => {
      try {
        const s = await api.backtestStatus(id);
        setStatus(s.status);
        setProgress(s.progress);
        if (s.status === "done") {
          clearInterval(interval);
          const res = await api.backtestResult(id);
          setResult(res);
        } else if (s.status === "error") {
          clearInterval(interval);
          setError(s.error || "backtest failed");
        }
      } catch (e) {
        clearInterval(interval);
        setError(String(e));
      }
    }, 3000);
  }

  return (
    <Card className="mb-4">
      <CardHeader title="Backtester" />
      <div className="p-4 space-y-3">
        <div>
          <label className="text-xs" style={{ color: "var(--text-dim)" }}>
            Days
          </label>
          <input
            type="number"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="ml-2 w-20 rounded border px-2 py-1 text-xs"
            style={{ background: "var(--bg)", borderColor: "var(--panel-border)", color: "var(--text)" }}
          />
        </div>
        <div className="flex flex-wrap gap-1">
          {pairs.map((p) => (
            <button
              key={p}
              onClick={() => toggle(p)}
              className="text-[10px] px-2 py-1 rounded border"
              style={{
                borderColor: "var(--panel-border)",
                background: selected.includes(p) ? "var(--green-bg)" : "transparent",
                color: selected.includes(p) ? "var(--green)" : "var(--text-dim)",
              }}
            >
              {p}
            </button>
          ))}
        </div>
        <button
          onClick={run}
          disabled={status === "running"}
          className="text-xs px-3 py-1.5 rounded border"
          style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
        >
          {status === "running" ? `Running… ${(progress * 100).toFixed(0)}%` : "Run backtest"}
        </button>
        {error && <p className="text-xs" style={{ color: "var(--red)" }}>{error}</p>}

        {result && (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge tone="neutral">{result.bars_processed} bars</Badge>
              <Badge tone={result.data_coverage_warning ? "amber" : "neutral"}>
                {result.actual_days_covered}d covered
              </Badge>
              <Badge tone={result.stats.pnl_net >= 0 ? "green" : "red"}>Net {fmtUsd(result.stats.pnl_net)}</Badge>
              <Badge tone="amber">Commission {fmtUsd(result.stats.commission_paid)}</Badge>
              <Badge tone="neutral">Win rate {fmtPct(result.stats.win_rate)}</Badge>
              <Badge tone="neutral">
                PF {result.stats.profit_factor === null ? "∞" : result.stats.profit_factor.toFixed(2)}
              </Badge>
            </div>
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ color: "var(--text-dim)" }}>
                    {["Symbol", "Trades", "Win%", "Gross", "Commission", "Net"].map((h) => (
                      <th key={h} className="text-left px-2 py-1">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(result.stats.by_symbol).map(([sym, s]) => (
                    <tr key={sym} className="border-t" style={{ borderColor: "var(--panel-border)" }}>
                      <td className="px-2 py-1 font-medium">{sym}</td>
                      <td className="px-2 py-1">{s.trades}</td>
                      <td className="px-2 py-1">{fmtPct(s.win_rate)}</td>
                      <td className="px-2 py-1">{fmtUsd(s.pnl_gross)}</td>
                      <td className="px-2 py-1" style={{ color: "var(--amber)" }}>
                        {fmtUsd(s.commission)}
                      </td>
                      <td className="px-2 py-1" style={{ color: s.pnl_net >= 0 ? "var(--green)" : "var(--red)" }}>
                        {fmtUsd(s.pnl_net)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
