import { Card, CardHeader, Badge } from "@/components/ui";
import type { Trade } from "@/lib/trading-api";
import { fmtUsd, fmtNum, fmtTime } from "@/lib/utils";

export function OpenPositionsPanel({ trades, onClose }: { trades: Trade[]; onClose: (id: number) => void }) {
  return (
    <Card className="mb-4">
      <CardHeader title={`Open Positions (${trades.length})`} />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: "var(--text-dim)" }}>
              {["Symbol", "Side", "Lots", "Entry", "SL", "TP1", "P&L", "Opened", "Flags", ""].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className="border-t" style={{ borderColor: "var(--panel-border)" }}>
                <td className="px-3 py-2 font-medium">{t.symbol}</td>
                <td className="px-3 py-2">
                  <Badge tone={t.side === "BULLISH" ? "green" : "red"}>{t.side}</Badge>
                </td>
                <td className="px-3 py-2">{t.lots}</td>
                <td className="px-3 py-2">{fmtNum(t.entry_price)}</td>
                <td className="px-3 py-2">{fmtNum(t.sl_price)}</td>
                <td className="px-3 py-2">{fmtNum(t.tp_price)}</td>
                <td className="px-3 py-2" style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                  {fmtUsd(t.pnl)}
                </td>
                <td className="px-3 py-2" style={{ color: "var(--text-dim)" }}>
                  {fmtTime(t.opened_at)}
                </td>
                <td className="px-3 py-2">
                  {t.be_moved && <Badge tone="amber">BE/Trail</Badge>}
                  {t.partial_taken && <Badge tone="green">TP1 taken</Badge>}
                </td>
                <td className="px-3 py-2">
                  <button
                    onClick={() => onClose(t.id)}
                    className="text-xs px-2 py-1 rounded border"
                    style={{ borderColor: "var(--panel-border)", color: "var(--text-dim)" }}
                  >
                    Close
                  </button>
                </td>
              </tr>
            ))}
            {trades.length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-4 text-center" style={{ color: "var(--text-dim)" }}>
                  No open positions
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
