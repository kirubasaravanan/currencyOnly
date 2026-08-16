import { Card, CardHeader, Badge } from "@/components/ui";
import type { PairCalibration } from "@/lib/trading-api";

function fmtSessions(windows: [number, number][]): string {
  return windows
    .map(([s, e]) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}-${String(Math.floor(e / 60)).padStart(2, "0")}:${String(e % 60).padStart(2, "0")}`)
    .join(", ");
}

export function PairCalibrationPanel({ calibration }: { calibration: Record<string, PairCalibration> }) {
  const pairs = Object.keys(calibration);
  return (
    <Card className="mb-4">
      <CardHeader title="Per-Pair Calibration (V109-derived starting points)" />
      <div className="overflow-x-auto max-h-96 overflow-y-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: "var(--text-dim)" }}>
              {["Pair", "Session (IST)", "SL mult", "TP mult", "SL pips", "Threshold", "Trail lock%", "Trend filter"].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium sticky top-0" style={{ background: "var(--panel)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pairs.map((p) => {
              const c = calibration[p];
              return (
                <tr key={p} className="border-t" style={{ borderColor: "var(--panel-border)" }}>
                  <td className="px-3 py-2 font-medium">{p}</td>
                  <td className="px-3 py-2" style={{ color: "var(--text-dim)" }}>
                    {fmtSessions(c.session_windows_ist)}
                  </td>
                  <td className="px-3 py-2">{c.stop_mult}</td>
                  <td className="px-3 py-2">{c.tp_mult}</td>
                  <td className="px-3 py-2">
                    {c.min_sl_pips}–{c.max_sl_pips}
                  </td>
                  <td className="px-3 py-2">{(c.confidence_threshold * 100).toFixed(0)}%</td>
                  <td className="px-3 py-2">{c.trail_lock_pct}%</td>
                  <td className="px-3 py-2">
                    <Badge tone={c.use_trend_filter ? "green" : "neutral"}>{c.use_trend_filter ? "on" : "off"}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
