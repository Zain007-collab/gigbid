import { useEffect, useState } from "react";
import { api, pct, type Analytics } from "@/lib/client";

export default function AnalyticsPage() {
  const [a, setA] = useState<Analytics | null>(null);
  useEffect(() => { api.GET("/api/analytics").then((r) => setA(r.data ?? null)); }, []);
  if (!a) return <><h1>Analytics</h1><div className="skeleton" /></>;
  const f = a.funnel;
  const stat = (label: string, v: string | number) => <div className="card"><div className="small muted">{label}</div><div style={{ fontSize: 24, fontWeight: 700 }}>{v}</div></div>;
  return (
    <>
      <h1>Analytics</h1>
      <div className="grid three">
        {stat("Proposals", a.proposals)}{stat("Reply rate", pct(a.proposals ? a.replies / a.proposals : null))}{stat("Hire rate", pct(a.proposals ? a.hires / a.proposals : null))}
        {stat("Connects spent", a.connects_spent)}{stat("Cost per proposal", a.cost_per_proposal_usd != null ? `$${a.cost_per_proposal_usd.toFixed(4)}` : "—")}
        {stat("p95 latency per job", a.p95_latency_job_s != null ? `${a.p95_latency_job_s}s` : "—")}
      </div>
      <section className="card" style={{ marginTop: 12 }}><h2>Funnel</h2>
        <table><tbody>{Object.entries(f).map(([k, v]) => <tr key={k}><td style={{ textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</td><td>{v}</td>
          <td style={{ width: "50%" }}><div style={{ background: "var(--accent)", height: 10, borderRadius: 4, width: `${f.seen ? (100 * v) / f.seen : 0}%` }} /></td></tr>)}</tbody></table></section>
      <section className="card" style={{ marginTop: 12 }}><h2>By category</h2>
        <table className="responsive"><thead><tr><th>Category</th><th>Proposals</th><th>Reply rate</th><th>Hire rate</th><th>Connects</th></tr></thead>
          <tbody>{a.by_category.map((c: any) => <tr key={c.category}><td data-label="Category">{c.category}</td><td data-label="Proposals">{c.proposals}</td>
            <td data-label="Reply">{pct(c.reply_rate)}</td><td data-label="Hire">{pct(c.hire_rate)}</td><td data-label="Connects">{c.connects}</td></tr>)}</tbody></table></section>
      <section className="card" style={{ marginTop: 12 }}><h2>Top rejection reasons</h2>
        {a.top_rejection_reasons.length ? <ul>{a.top_rejection_reasons.map((r: any) => <li key={r.reason}>{r.reason} ({r.count})</li>)}</ul> : <p className="muted">No rejections yet.</p>}</section>
    </>
  );
}
