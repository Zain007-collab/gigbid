import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errText, money, type InboxItem } from "@/lib/client";
import { ActionBadge, Fit, RiskBadge } from "@/components/Badges";

export default function Inbox() {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [filter, setFilter] = useState("all");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => {
    const r = await api.GET("/api/jobs");
    if (r.error) setErr(errText(r.error)); else setItems(r.data);
  };
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  const scout = async () => {
    setMsg("Scouting…");
    const r = await api.POST("/api/jobs/scout");
    const e = (r as { error?: unknown }).error;
    setMsg(e ? errText(e) : r.data?.detail ?? null);
    load();
  };
  const shown = (items ?? []).filter((i) => filter === "all" || (filter === "approval" ? i.status === "awaiting_approval" : filter === "skipped" ? i.status === "skipped" : i.status !== "skipped"));
  return (
    <>
      <div className="row"><h1>Job inbox</h1><span className="spacer" />
        <label className="row small" style={{ display: "flex" }}>Show
          <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }} aria-label="Filter">
            <option value="all">All</option><option value="active">Not skipped</option>
            <option value="approval">Needs approval</option><option value="skipped">Skipped</option></select></label>
        <button className="primary" onClick={scout}>Scout now</button></div>
      {msg && <div className="banner info" role="status">{msg}</div>}
      {err && <div className="banner bad" role="alert">{err}</div>}
      {items === null ? <div className="card"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>
        : shown.length === 0 ? <div className="card"><p>No jobs yet.</p><p className="muted">Click <strong>Scout now</strong> to run your saved searches. New jobs are analysed in the background and ranked by fit.</p></div>
        : (
          <div className="card" style={{ padding: 0 }}>
            <table className="responsive" data-testid="inbox-table">
              <thead><tr><th>Job</th><th>Fit</th><th>Risk</th><th>Recommendation</th><th>Price</th><th>Connects</th><th></th></tr></thead>
              <tbody>{shown.map((i) => (
                <tr key={i.job_id}>
                  <td data-label="Job"><Link to={`/jobs/${i.job_id}`}>{i.title ?? i.job_id}</Link>
                    <div className="small muted">{i.category ?? ""} · budget {money(i.budget_min)}–{money(i.budget_max)}</div></td>
                  <td data-label="Fit"><Fit v={i.fit} /></td>
                  <td data-label="Risk"><RiskBadge risk={i.risk} flags={i.risk_flags} /></td>
                  <td data-label="Recommendation"><ActionBadge action={i.action} status={i.status} /><div className="small muted">{i.summary}</div></td>
                  <td data-label="Price">{money(i.price)}{i.rate_type === "hourly" ? "/h" : ""}</td>
                  <td data-label="Connects">{i.connects ?? "—"}</td>
                  <td data-label="">{i.status === "awaiting_approval"
                    ? <Link className="btn" to={`/studio/${i.job_id}`}>Review</Link>
                    : i.status !== "skipped" && i.status !== "submitted"
                      ? <button onClick={async () => { await api.POST("/api/jobs/{job_id}/skip", { params: { path: { job_id: i.job_id } } }); load(); }}>Skip</button>
                      : null}</td>
                </tr>))}</tbody>
            </table>
          </div>)}
    </>
  );
}
