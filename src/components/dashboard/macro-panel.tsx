import { Card, CardHeader, Badge } from "@/components/ui";
import type { MacroSnapshot } from "@/lib/trading-api";

export function MacroPanel({ macro }: { macro: MacroSnapshot | null }) {
  if (!macro) {
    return (
      <Card className="mb-4">
        <CardHeader title="Macro" />
        <div className="p-4 text-sm" style={{ color: "var(--text-dim)" }}>
          Loading…
        </div>
      </Card>
    );
  }
  const biasTone = macro.usd_bias === "usd_bullish" ? "green" : macro.usd_bias === "usd_bearish" ? "red" : "neutral";
  return (
    <Card className="mb-4">
      <CardHeader title="Macro" />
      <div className="p-4 space-y-3 text-sm">
        <div className="flex items-center justify-between">
          <span style={{ color: "var(--text-dim)" }}>DXY direction</span>
          <Badge tone={macro.dxy_direction === "up" ? "green" : "red"}>{macro.dxy_direction}</Badge>
        </div>
        <div className="flex items-center justify-between">
          <span style={{ color: "var(--text-dim)" }}>US10Y direction</span>
          <Badge tone={macro.us10y_direction === "up" ? "green" : "red"}>{macro.us10y_direction}</Badge>
        </div>
        <div className="flex items-center justify-between">
          <span style={{ color: "var(--text-dim)" }}>USD bias</span>
          <Badge tone={biasTone}>{macro.usd_bias}</Badge>
        </div>
        <div className="flex items-center justify-between">
          <span style={{ color: "var(--text-dim)" }}>News blackout</span>
          <Badge tone={macro.news_blackout.blocked ? "red" : "green"}>
            {macro.news_blackout.blocked ? macro.news_blackout.event : "clear"}
          </Badge>
        </div>
        {macro.calendar_next_24h.length > 0 && (
          <div>
            <p className="text-xs mb-1" style={{ color: "var(--text-dim)" }}>
              Next 24h high-impact events
            </p>
            <ul className="space-y-1 text-xs">
              {macro.calendar_next_24h.slice(0, 5).map((e, i) => (
                <li key={i} style={{ color: "var(--text)" }}>
                  {e.country} — {e.title}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}
