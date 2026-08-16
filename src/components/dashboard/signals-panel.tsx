import { Card, CardHeader, Badge } from "@/components/ui";
import type { Signal } from "@/lib/trading-api";
import { fmtNum, fmtPct } from "@/lib/utils";

export function SignalsPanel({ signals }: { signals: Record<string, Signal> }) {
  const entries = Object.entries(signals);
  return (
    <Card className="mb-4">
      <CardHeader title={`Live Signals (${entries.length})`} />
      <div className="p-4 space-y-3">
        {entries.length === 0 && (
          <p className="text-sm" style={{ color: "var(--text-dim)" }}>
            No signals have triggered yet this session — the hybrid gate (session + sweep + MSS + trend + confluence
            score) is strict by design.
          </p>
        )}
        {entries.map(([symbol, s]) => (
          <div key={symbol} className="rounded border p-3" style={{ borderColor: "var(--panel-border)" }}>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{symbol}</span>
                <Badge tone={s.side === "BULLISH" ? "green" : "red"}>{s.side}</Badge>
                <Badge tone="neutral">{s.session}</Badge>
                {s.adr_exhausted && <Badge tone="amber">ADR exhausted</Badge>}
              </div>
              <span className="text-xs" style={{ color: "var(--text-dim)" }}>
                confidence {fmtPct(s.confidence * 100)} / threshold {fmtPct(s.threshold * 100)}
              </span>
            </div>
            <div className="grid grid-cols-4 gap-2 text-xs mb-2" style={{ color: "var(--text-dim)" }}>
              <span>Entry: {fmtNum(s.entry_price)}</span>
              <span>SL: {fmtNum(s.sl_price)}</span>
              <span>TP1: {fmtNum(s.tp_price)}</span>
              <span>TP2: {fmtNum(s.tp2_price)}</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {Object.entries(s.factor_scores || {}).map(([k, v]) => (
                <span
                  key={k}
                  className="text-[10px] px-1.5 py-0.5 rounded"
                  style={{
                    background: v > 0 ? "var(--green-bg)" : "var(--panel-border)",
                    color: v > 0 ? "var(--green)" : "var(--text-dim)",
                  }}
                >
                  {k}: {v.toFixed(2)}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
