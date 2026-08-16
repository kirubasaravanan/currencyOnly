import { Card, StatTile } from "@/components/ui";
import type { Stats } from "@/lib/trading-api";
import { fmtUsd, fmtPct } from "@/lib/utils";

export function KpiRow({ stats }: { stats: Stats }) {
  return (
    <Card className="mb-4 grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 divide-x" style={{ borderColor: "var(--panel-border)" }}>
      <StatTile label="Equity" value={fmtUsd(stats.equity)} />
      <StatTile label="Gross P&L" value={fmtUsd(stats.pnl_gross)} tone={stats.pnl_gross >= 0 ? "green" : "red"} />
      <StatTile label="Commission paid" value={fmtUsd(stats.commission_paid)} tone="amber" />
      <StatTile label="Net P&L" value={fmtUsd(stats.pnl_net)} tone={stats.pnl_net >= 0 ? "green" : "red"} />
      <StatTile label="Win rate" value={fmtPct(stats.win_rate)} sub={`${stats.wins}W / ${stats.losses}L`} />
      <StatTile label="Profit factor" value={stats.profit_factor === null ? "∞" : stats.profit_factor.toFixed(2)} />
      <StatTile label="Expectancy" value={fmtUsd(stats.expectancy)} />
      <StatTile label="Drawdown" value={fmtPct(stats.drawdown_pct)} tone={stats.drawdown_pct > 5 ? "red" : "neutral"} />
    </Card>
  );
}
