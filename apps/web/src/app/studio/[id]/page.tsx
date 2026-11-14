import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errText, money, type JobDetail } from "@/lib/client";
import EvidenceHover, { type ClaimView } from "@/components/EvidenceHover";
import ApprovalPanel, { type Decision } from "@/components/ApprovalPanel";
import RunTimeline from "@/components/RunTimeline";
import { useMe } from "@/components/Layout";

export default function Studio() {
  const { id = "" } = useParams();
  const { me } = useMe();
  const [d, setD] = useState<JobDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [regen, setRegen] = useState<Record<number, string>>({});
  const load = async () => {
    const r = await api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } });
    if (r.error) setErr(errText(r.error)); else setD(r.data);
  };
  useEffect(() => { load(); }, [id]);
  const p = d?.pending_approval as Record<string, any> | null | undefined;
  async function decide(dec: Decision) {
    setBusy(true);
    const r = await api.POST("/api/approvals/proposal/{job_id}", { params: { path: { job_id: id } }, body: dec });
    setBusy(false);
    if (r.error) return setErr(errText(r.error));
    setMsg(dec.action === "reject" ? "Rejected. Nothing was sent." : "Approved — submitting (exactly once)…");
    setTimeout(load, 1500);
  }
  async function regenerate(i: number) {
    const r = await api.POST("/api/proposals/{job_id}/regenerate", { params: { path: { job_id: id } }, body: { sentence_index: i } });
    if (r.data) setRegen((x) => ({ ...x, [i]: r.data!.text }));
  }
  const tid = `bid:${me?.id}:${id}`;
  return (
    <>
      <div className="row"><h1>Proposal studio</h1><span className="spacer" /><Link to={`/jobs/${id}`}>Job detail</Link></div>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {msg && <div className="banner info" role="status" data-testid="studio-status">{msg}</div>}
      {d?.status === "submitted" && <div className="banner info" data-testid="submitted">Submitted · {JSON.stringify(d.submission)}</div>}
      {d?.status === "rejected" && <div className="banner warn" data-testid="rejected">Rejected — {d.attention_reason}</div>}
      {d?.status === "blocked" && <div className="banner bad">Compliance Guard blocked this proposal: {d.attention_reason}</div>}
      {!d ? <div className="card"><div className="skeleton" /><div className="skeleton" /></div> : !p ? (
        <div className="card"><p>No draft is waiting for approval for this job (status: <strong>{d.status}</strong>).</p><RunTimeline threadId={tid} onFinished={load} /></div>
      ) : (
        <div className="grid two">
          <section className="card">
            <h2>{p.job_title}</h2>
            {p.note && <div className="banner warn">{p.note}</div>}
            <p className="small muted">Hover or focus a sentence to see the portfolio evidence behind it. Blue = supported, amber = partly, red = unsupported.</p>
            <p style={{ lineHeight: 1.8 }} data-testid="draft-text">
              {(p.claims as ClaimView[]).map((c, i) => <span key={i}><EvidenceHover claim={c} index={i} />
                <button className="small" aria-label={`Regenerate sentence ${i + 1}`} onClick={() => regenerate(i)} style={{ padding: "0 6px", marginRight: 6 }}>↻</button>
                {regen[i] && <span className="badge" title="suggested rewrite — copy it in via Edit">suggestion: {regen[i]}</span>}</span>)}
            </p>
            {(p.screening_answers ?? []).length > 0 && <><h2>Screening answers</h2>
              {p.screening_answers.map((a: any) => <div key={a.question_id} className="small"><strong>{a.question}</strong><p>{a.answer}</p></div>)}</>}
            <h2>Critic and Compliance</h2>
            <ul className="small">
              {(p.critiques ?? []).map((c: any, i: number) => <li key={i}>Critic v{c.draft_version}: {c.approve ? "approved" : "revise"} {c.issues?.length ? `— ${c.issues.join("; ")}` : ""}</li>)}
              {(p.compliance?.violations ?? []).map((v: any, i: number) => <li key={`v${i}`}>Compliance {v.rule}: {v.action} “{v.span}”</li>)}
              {!p.compliance?.violations?.length && <li>Compliance: no violations</li>}
              <li>Claim verification removed {p.unsupported_before_verify ?? 0} unsupported claim(s) before the Critic saw the draft.</li>
            </ul>
            <h2>Price</h2>
            <p className="small"><strong>{money(p.price?.amount)}</strong> {p.price?.rate_type} · {p.price?.rationale}</p>
            <p className="small">Risk: {p.risk?.summary}</p>
          </section>
          <ApprovalPanel text={p.text} preview={p.preview} onDecide={decide} busy={busy} />
        </div>)}
    </>
  );
}
