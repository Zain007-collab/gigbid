import { useEffect, useRef, useState } from "react";

type Ev = { type: string; node?: string; ms?: number; status?: string | null; tools?: { tool: string; ok: boolean; ms: number }[];
  llm?: { model?: string; tokens_in?: number; tokens_out?: number; usd?: number }[]; error?: string | null; trace_id?: string; usd?: number; tokens?: number; latency_s?: number };

/** Live node-by-node progress streamed from LangGraph over Server-Sent Events. */
export default function RunTimeline({ threadId, onFinished }: { threadId: string; onFinished?: () => void }) {
  const [events, setEvents] = useState<Ev[]>([]);
  const [live, setLive] = useState(false);
  const done = useRef(onFinished);
  done.current = onFinished;
  useEffect(() => {
    setEvents([]);
    const es = new EventSource(`/api/runs/${encodeURIComponent(threadId)}/stream`, { withCredentials: true });
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as Ev;
      setEvents((xs) => [...xs, ev]);
      if (ev.type === "run_finished" || ev.type === "interrupt") done.current?.();
    };
    return () => es.close();
  }, [threadId]);
  return (
    <div className="card" aria-live="polite" data-testid="run-timeline">
      <div className="row"><strong>Agent run</strong><span className="spacer" />
        <span className={`badge ${live ? "ok" : ""}`}>{live ? "live" : "connecting…"}</span></div>
      {events.length === 0 && <p className="muted small">Waiting for the agents to start…</p>}
      <ol className="timeline small">
        {events.map((e, i) => (
          <li key={i}>
            {e.type === "node" && <>
              <strong>{e.node}</strong> <span className="muted">{e.ms != null ? `${e.ms} ms` : ""}</span>
              {e.status && <span className="badge" style={{ marginLeft: 6 }}>{e.status}</span>}
              {(e.tools ?? []).map((t, k) => <span key={k} className="muted"> · {t.tool}{t.ok ? "" : " ✗"}</span>)}
              {(e.llm ?? []).map((l, k) => <span key={k} className="muted"> · {l.model} {l.tokens_in ?? 0}+{l.tokens_out ?? 0} tok</span>)}
            </>}
            {e.type === "run_started" && <span className="muted">run started · trace <code>{e.trace_id?.slice(0, 12)}</code></span>}
            {e.type === "interrupt" && <strong>⏸ Waiting for your approval</strong>}
            {e.type === "run_finished" && <span>finished: <strong>{e.status}</strong>{e.error ? ` — ${e.error}` : ""}
              <span className="muted"> · {e.latency_s}s · {e.tokens} tokens · ${e.usd}</span></span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
