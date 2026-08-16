"use client";

import { Card, CardHeader } from "@/components/ui";
import type { Trade } from "@/lib/trading-api";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export function EquityCurve({ trades, initialBalance }: { trades: Trade[]; initialBalance: number }) {
  const closed = [...trades].sort((a, b) => (a.closed_at || "").localeCompare(b.closed_at || ""));
  let running = initialBalance;
  const points = [{ i: 0, equity: initialBalance, label: "start" }];
  closed.forEach((t, idx) => {
    running += t.pnl;
    points.push({ i: idx + 1, equity: Math.round(running * 100) / 100, label: t.symbol });
  });

  return (
    <Card className="mb-4">
      <CardHeader title="Equity Curve" />
      <div className="p-4" style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--panel-border)" />
            <XAxis dataKey="i" stroke="var(--text-dim)" fontSize={11} />
            <YAxis domain={["auto", "auto"]} stroke="var(--text-dim)" fontSize={11} width={70} />
            <Tooltip
              contentStyle={{ background: "var(--panel)", border: "1px solid var(--panel-border)", fontSize: 12 }}
              labelFormatter={(_, p) => (p?.[0]?.payload?.label ? `after ${p[0].payload.label}` : "")}
            />
            <Line type="stepAfter" dataKey="equity" stroke="var(--accent)" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
