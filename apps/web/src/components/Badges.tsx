import { pct } from "@/lib/client";

export function RiskBadge({ risk, flags }: { risk?: number | null; flags?: string[] }) {
  if (risk == null) return <span className="badge">pending</span>;
  const cls = risk >= 0.7 ? "bad" : risk > 0.4 ? "warn" : "ok";
  const label = risk >= 0.7 ? "High risk" : risk > 0.4 ? "Caution" : "Low risk";
  return <span className={`badge ${cls}`} title={(flags ?? []).join(", ")}>{label}{flags?.length ? ` · ${flags.join(", ")}` : ""}</span>;
}

export function ActionBadge({ action, status }: { action?: string | null; status: string }) {
  const map: Record<string, string> = { awaiting_approval: "warn", submitted: "ok", skipped: "", watch: "", blocked: "bad",
    dead_letter: "bad", needs_attention: "bad", degraded_queued: "warn", rejected: "", running: "", queued: "" };
  const text = status === "awaiting_approval" ? "Needs your approval" : status === "skipped" ? `Skip` :
    status.replace(/_/g, " ");
  return <span className={`badge ${map[status] ?? ""}`}>{action === "bid" && status === "awaiting_approval" ? "Bid · " : ""}{text}</span>;
}

export const Fit = ({ v }: { v?: number | null }) => <strong>{pct(v)}</strong>;
