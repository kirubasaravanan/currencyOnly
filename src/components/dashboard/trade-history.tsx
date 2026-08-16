import { Card, CardHeader, Badge } from "@/components/ui";
import type { Trade } from "@/lib/trading-api";
import { fmtUsd, fmtNum, fmtTime } from "@/lib/utils";

export function TradeHistoryPanel({ trades }: { trades: Trade[] }) {
  const recent = [...trades].reverse().slice(0, 30);
  return (
    <Card className="mb-4">
      <CardHeader title="Trade History" />
      <div className="overflow-x-auto max-h-96 overflow-y-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: "var(--text-dim)" }}>
              {["Symbol", "Side", "Gross", "Commission", "Net", "Reason", "Closed"].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium sticky top-0" style={{ background: "var(--panel)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {recent.map((t) => (
              <tr key={t.id} className="border-t" style={{ borderColor: "var(--panel-border)" }}>
                <td className="px-3 py-2 font-medium">{t.symbol}</td>
                <td className="px-3 py-2">
                  <Badge tone={t.side === "BULLISH" ? "green" : "red"}>{t.side}</Badge>
                </td>
                <td className="px-3 py-2">{fmtUsd(t.pnl_gross)}</td>
                <td className="px-3 py-2" style={{ color: "var(--amber)" }}>
                  {fmtUsd(t.commission_paid)}
                </td>
                <td className="px-3 py-2 font-medium" style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                  {fmtUsd(t.pnl)}
                </td>
                <td className="px-3 py-2" style={{ color: "var(--text-dim)" }}>
                  {t.reason}
                </td>
                <td className="px-3 py-2" style={{ color: "var(--text-dim)" }}>
                  {fmtTime(t.closed_at)}
                </td>
              </tr>
            ))}
            {recent.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center" style={{ color: "var(--text-dim)" }}>
                  No closed trades yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
