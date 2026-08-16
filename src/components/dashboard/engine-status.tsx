import { Card, Badge } from "@/components/ui";
import type { Snapshot } from "@/lib/trading-api";
import { fmtTime } from "@/lib/utils";

export function EngineStatusBar({ engine }: { engine: Snapshot["engine"] | null }) {
  if (!engine) {
    return (
      <Card className="mb-4 px-4 py-3">
        <Badge tone="red">Engine offline</Badge>
      </Card>
    );
  }
  return (
    <Card className="mb-4 px-4 py-3 flex items-center gap-4">
      <Badge tone={engine.running ? "green" : "red"}>{engine.running ? "Engine running" : "Engine stopped"}</Badge>
      <span className="text-xs" style={{ color: "var(--text-dim)" }}>
        Scans: {engine.scan_count} · Last scan: {engine.last_scan_at ? fmtTime(new Date(engine.last_scan_at * 1000).toISOString()) : "—"}
      </span>
      {engine.last_error && <Badge tone="amber">{engine.last_error}</Badge>}
    </Card>
  );
}
