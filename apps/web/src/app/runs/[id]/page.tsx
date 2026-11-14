import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "@/lib/client";
import RunTimeline from "@/components/RunTimeline";

export default function RunPage() {
  const { id = "" } = useParams();
  const tid = decodeURIComponent(id);
  const [trace, setTrace] = useState<any | null>(null);
  const [hist, setHist] = useState<any[]>([]);
  const load = async () => {
    const t = await api.GET("/api/runs/{thread_id}/trace", { params: { path: { thread_id: tid } } });
    setTrace(t.data ?? null);
    const h = await api.GET("/api/runs/{thread_id}/history", { params: { path: { thread_id: tid } } });
    setHist((h.data as any[]) ?? []);
  };
  useEffect(() => { load(); }, [tid]);
  const langfuse = (import.meta as any).env?.VITE_LANGFUSE_URL as string | undefined;
  return (
    <>
      <h1>Agent run</h1>
      <p className="small muted">Thread <code>{tid}</code>{trace?.runs?.length ? <> · trace id <code>{trace.runs.at(-1).trace_id}</code>
        {langfuse && <> · <a href={`${langfuse}/trace/${trace.runs.at(-1).trace_id}`} target="_blank" rel="noreferrer">open full trace</a></>}</> : null}</p>
      <RunTimeline threadId={tid} onFinished={load} />
      <section className="card" style={{ marginTop: 12 }}><h2>Execution trace</h2>
        <table className="responsive"><thead><tr><th>Node</th><th>Time</th><th>Tool calls</th><th>LLM</th><th>Status</th></tr></thead>
          <tbody>{(trace?.trace ?? []).map((t: any, i: number) => (
            <tr key={i}><td data-label="Node">{t.node}</td><td data-label="Time">{t.ms} ms</td>
              <td data-label="Tool calls" className="small">{(t.tools ?? []).map((c: any, k: number) => <div key={k}>{c.tool} {c.ok ? "✓" : "✗"} <span className="muted">{c.ms}ms</span></div>)}</td>
              <td data-label="LLM" className="small">{(t.llm ?? []).map((l: any, k: number) => <div key={k}>{l.model ?? "—"} {l.tokens_in ?? 0}+{l.tokens_out ?? 0} tok ${l.usd ?? 0}{l.error ? ` ✗ ${l.error}` : ""}</div>)}</td>
              <td data-label="Status">{t.status ?? ""}{t.error ? <span className="badge bad">{t.error}</span> : null}</td></tr>))}</tbody></table>
        <h2>Cost by node</h2>
        <pre className="small">{JSON.stringify(trace?.usage ?? {}, null, 1)}</pre>
      </section>
      <section className="card" style={{ marginTop: 12 }}><h2>Checkpoints (replay from any node)</h2>
        <table className="responsive"><thead><tr><th>Step</th><th>Next node</th><th>Status</th><th></th></tr></thead>
          <tbody>{hist.map((h) => (
            <tr key={h.checkpoint_id}><td data-label="Step">{h.step}</td><td data-label="Next">{(h.next ?? []).join(", ") || "—"}</td><td data-label="Status">{h.status ?? ""}</td>
              <td>{h.next?.length ? <button className="small" onClick={() => api.POST("/api/runs/{thread_id}/replay/{checkpoint_id}", { params: { path: { thread_id: tid, checkpoint_id: h.checkpoint_id } } })}>Replay from here</button> : null}</td></tr>))}</tbody></table>
      </section>
    </>
  );
}
