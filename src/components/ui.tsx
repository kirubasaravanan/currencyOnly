import { cn } from "@/lib/utils";

export function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn("rounded-lg border", className)}
      style={{ background: "var(--panel)", borderColor: "var(--panel-border)" }}
    >
      {children}
    </div>
  );
}

export function CardHeader({ title, right }: { title: string; right?: React.ReactNode }) {
  return (
    <div
      className="flex items-center justify-between px-4 py-3 border-b"
      style={{ borderColor: "var(--panel-border)" }}
    >
      <h2 className="text-sm font-semibold tracking-wide" style={{ color: "var(--text)" }}>
        {title}
      </h2>
      {right}
    </div>
  );
}

type BadgeTone = "green" | "red" | "amber" | "neutral";

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: BadgeTone }) {
  const styles: Record<BadgeTone, { bg: string; fg: string }> = {
    green: { bg: "var(--green-bg)", fg: "var(--green)" },
    red: { bg: "var(--red-bg)", fg: "var(--red)" },
    amber: { bg: "var(--amber-bg)", fg: "var(--amber)" },
    neutral: { bg: "var(--panel-border)", fg: "var(--text-dim)" },
  };
  const s = styles[tone];
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium"
      style={{ background: s.bg, color: s.fg }}
    >
      {children}
    </span>
  );
}

export function StatTile({
  label,
  value,
  tone = "neutral",
  sub,
}: {
  label: string;
  value: string;
  tone?: BadgeTone;
  sub?: string;
}) {
  const fg = tone === "green" ? "var(--green)" : tone === "red" ? "var(--red)" : tone === "amber" ? "var(--amber)" : "var(--text)";
  return (
    <div className="flex flex-col gap-1 px-4 py-3">
      <span className="text-xs" style={{ color: "var(--text-dim)" }}>
        {label}
      </span>
      <span className="text-lg font-semibold" style={{ color: fg }}>
        {value}
      </span>
      {sub && (
        <span className="text-xs" style={{ color: "var(--text-dim)" }}>
          {sub}
        </span>
      )}
    </div>
  );
}
