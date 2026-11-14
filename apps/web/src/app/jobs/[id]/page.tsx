import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errText, money, pct, type JobDetail } from "@/lib/client";
import { RiskBadge } from "@/components/Badges";
import RunTimeline from "@/components/RunTimeline";
import { useMe } from "@/components/Layout";

type Match = { requirement: string; supported: boolean; reason: string; evidence: { id: string; type: string; project?: string; text: string }[] };

export default function JobPage() {
  const { id = "" } = useParams();
  const { me } = useMe();
  const [d, setD] = useState<JobDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => {
    const r = await api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } });
    if (r.error) setErr(errText(r.error)); else { setErr(null); setD(r.data); }
  };
  useEffect(() => { load(); }, [id]);
  const analyze = async (force = false) => {
    await api.POST("/api/jobs/{job_id}/analyze", { params: { path: { job_id: id }, query: { force_bid: force } } });
    load();
  };
  const tid = `bid:${me?.id}:${id}`;
  const job = d?.job as Record<string, any> | null;
  const fit = d?.fit as { fit_score: number; matches: Match[]; gaps: string[] } | null;
  const risk = d?.risk as { risk_score: number; client_quality: number; flags: { kind: string; evidence: string; severity: number }[]; injection_detected: boolean; summary: string } | null;
  const price = d?.price as Record<string, any> | null;
  const decision = d?.decision as { action: string; reasons: string[] } | null;
  return (
    <>
      <div className="row"><h1>{job?.title ?? id}</h1><span className="spacer" />
        {d?.pending_approval && <Link className="btn" to={`/studio/${id}`}>Open proposal studio</Link>}
        <button onClick={() => analyze(false)}>Re-analyse</button>
        {decision && decision.action !== "bid" && <button onClick={() => analyze(true)}>Draft anyway</button>}
        <Link className="btn" to={`/runs/${encodeURIComponent(tid)}`}>Trace</Link></div>
      {err && <div className="banner warn">{err} <button onClick={() => analyze(false)}>Analyse now</button></div>}
      {d?.attention_reason && <div className="banner warn" role="status">{d.attention_reason}</div>}
      <RunTimeline threadId={tid} onFinished={load} />
      {!d ? null : <>
        <div className="grid two" style={{ marginTop: 12 }}>
          <section className="card"><h2>Bid or skip</h2>
            <p><span className={`badge ${decision?.action === "bid" ? "ok" : ""}`}>{decision?.action ?? d.status}</span> · fit <strong>{pct(fit?.fit_score)}</strong></p>
            <ul>{(decision?.reasons ?? []).map((r) => <li key={r}>{r}</li>)}</ul></section>
          <section className="card"><h2>Risk report</h2>
            <p><RiskBadge risk={risk?.risk_score} /> client quality {pct(risk?.client_quality)}
              {risk?.injection_detected && <span className="badge bad" style={{ marginLeft: 6 }}>prompt injection detected — ignored</span>}</p>
            <ul>{(risk?.flags ?? []).map((f, i) => <li key={i}><strong>{f.kind}</strong>: “{f.evidence}”</li>)}</ul>
            {!risk?.flags?.length && <p className="muted">No risk signals.</p>}</section>
        </div>
        <section className="card" style={{ marginTop: 12 }}><h2>Requirement → evidence</h2>
          <table className="responsive" data-testid="evidence-matrix"><thead><tr><th>Requirement</th><th>Proven by</th><th>Status</th></tr></thead>
            <tbody>{(fit?.matches ?? []).map((m, i) => (
              <tr key={i}><td data-label="Requirement">{m.requirement}</td>
                <td data-label="Proven by">{m.supported ? m.evidence.map((e) => <div key={e.id} className="small"><span className="badge">{e.type}</span> <code>{e.id}</code></div>) : <span className="muted small">—</span>}</td>
                <td data-label="Status"><span className={`badge ${m.supported ? "ok" : "warn"}`}>{m.supported ? "proven" : "gap"}</span><div className="small muted">{m.reason}</div></td></tr>))}</tbody></table>
        </section>
        <div className="grid two" style={{ marginTop: 12 }}>
          <section className="card"><h2>Price</h2>
            {price ? <><p><strong>{money(price.amount)}</strong>{price.rate_type === "hourly" ? "/h" : " fixed"} · {price.connects_cost} Connects {price.connects_worth_it ? "" : <span className="badge warn">not worth the Connects</span>}</p>
              <p className="small muted">{price.rationale}</p></> : <p className="muted">Priced only for jobs worth bidding on.</p>}</section>
          <section className="card"><h2>Job spec</h2>
            {d.job_spec ? <><p className="small"><strong>Deliverables:</strong> {((d.job_spec as any).deliverables ?? []).join(", ") || "—"}</p>
              <p className="small"><strong>Skills:</strong> {((d.job_spec as any).skills ?? []).join(", ")}</p>
              <p className="small"><strong>Screening:</strong> {((d.job_spec as any).screening_questions ?? []).map((q: any) => q.text).join(" · ") || "none"}</p></> : null}
            <details><summary className="small">Original post (untrusted)</summary><pre className="small" style={{ whiteSpace: "pre-wrap" }}>{job?.description}</pre></details></section>
        </div>
      </>}
    </>
  );
}
