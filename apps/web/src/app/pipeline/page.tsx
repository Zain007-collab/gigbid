import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errText, money, type PipelineCard } from "@/lib/client";

const COLS = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"] as const;

export default function Pipeline() {
  const [cards, setCards] = useState<PipelineCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => {
    const r = await api.GET("/api/pipeline");
    const e = (r as { error?: unknown }).error;
    if (e || !r.data) setErr(errText(e ?? "Marketplace unavailable")); else { setErr(null); setCards(r.data); }
  };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  return (
    <>
      <div className="row"><h1>Pipeline</h1><span className="spacer" /><button onClick={load}>Refresh</button></div>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {cards === null && !err ? <div className="skeleton" /> : (
        <div className="kanban" data-testid="kanban">
          {COLS.map((c) => (
            <div key={c} className="col" aria-label={c}>
              <div className="row"><strong style={{ textTransform: "capitalize" }}>{c}</strong><span className="spacer" />
                <span className="badge">{(cards ?? []).filter((x) => x.column === c).length}</span></div>
              {(cards ?? []).filter((x) => x.column === c).map((x) => (
                <div key={`${x.job_id}${x.proposal_id}`} className="card small" style={{ marginTop: 8, padding: 8 }}>
                  <Link to={x.column === "drafted" ? `/studio/${x.job_id}` : `/jobs/${x.job_id}`}>{x.title ?? x.job_id}</Link>
                  <div className="muted">{money(x.bid)} · {x.connects ?? "—"} Connects</div>
                  {x.thread_id && ["replied", "interview"].includes(x.column) && <Link to="/messages">Open thread</Link>}
                </div>))}
            </div>))}
        </div>)}
    </>
  );
}
