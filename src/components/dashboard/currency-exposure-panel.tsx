import { Card, CardHeader, Badge } from "@/components/ui";
import type { Trade } from "@/lib/trading-api";

function netExposure(openTrades: Trade[]): Record<string, number> {
  const exposure: Record<string, number> = {};
  for (const t of openTrades) {
    const sign = t.side === "BULLISH" ? 1 : -1;
    const base = t.symbol.slice(0, 3);
    const quote = t.symbol.slice(3, 6);
    exposure[base] = (exposure[base] || 0) + sign;
    exposure[quote] = (exposure[quote] || 0) - sign;
  }
  return exposure;
}

export function CurrencyExposurePanel({ openTrades }: { openTrades: Trade[] }) {
  const exposure = netExposure(openTrades);
  const entries = Object.entries(exposure).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return (
    <Card className="mb-4">
      <CardHeader title="Net Currency Exposure" />
      <div className="p-4">
        {entries.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-dim)" }}>
            No open positions — no net exposure.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {entries.map(([ccy, net]) => (
              <Badge key={ccy} tone={net > 0 ? "green" : net < 0 ? "red" : "neutral"}>
                {ccy} {net > 0 ? `+${net}` : net}
              </Badge>
            ))}
          </div>
        )}
        <p className="text-xs mt-3" style={{ color: "var(--text-dim)" }}>
          Max 2 open trades sharing the same dominant currency exposure (either direction) — generalized beyond a
          fixed USD-only correlation group since this universe is heavy on non-USD crosses.
        </p>
      </div>
    </Card>
  );
}
