import { Badge } from "@/components/ui";

export function Header({
  autoRefresh,
  onToggleAutoRefresh,
  onRefresh,
  onReset,
  lastUpdated,
}: {
  autoRefresh: boolean;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
  onReset: () => void;
  lastUpdated: string;
}) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold" style={{ color: "var(--text)" }}>
          currencyOnly
        </h1>
        <Badge tone="neutral">FX paper trading — 17 pairs</Badge>
        <Badge tone="amber">No real orders, ever</Badge>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs" style={{ color: "var(--text-dim)" }}>
          {lastUpdated}
        </span>
        <button
          onClick={onToggleAutoRefresh}
          className="text-xs px-3 py-1.5 rounded border"
          style={{ borderColor: "var(--panel-border)", color: autoRefresh ? "var(--green)" : "var(--text-dim)" }}
        >
          {autoRefresh ? "Auto-refresh ON" : "Auto-refresh OFF"}
        </button>
        <button
          onClick={onRefresh}
          className="text-xs px-3 py-1.5 rounded border"
          style={{ borderColor: "var(--panel-border)", color: "var(--text)" }}
        >
          Refresh
        </button>
        <button
          onClick={onReset}
          className="text-xs px-3 py-1.5 rounded border"
          style={{ borderColor: "var(--red)", color: "var(--red)" }}
        >
          Reset account
        </button>
      </div>
    </div>
  );
}
