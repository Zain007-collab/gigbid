import { useEffect, useState } from "react";
import { api, errText, type Conversation } from "@/lib/client";
import ApprovalPanel, { type Decision } from "@/components/ApprovalPanel";

export default function Messages() {
  const [threads, setThreads] = useState<Conversation[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<any[]>([]);
  const [pending, setPending] = useState<any | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = async () => { const r = await api.GET("/api/messages"); if (r.data) setThreads(r.data); };
  const open = async (t: string) => {
    setSel(t); setPending(null);
    const m = await api.GET("/api/messages/{thread_id}", { params: { path: { thread_id: t } } });
    setMsgs(m.data ?? []);
    const p = await api.GET("/api/approvals/message/{thread_id}", { params: { path: { thread_id: t } } });
    setPending(p.data ?? null);
  };
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);
  const sync = async () => {
    const r = await api.POST("/api/messages/sync");
    const e = (r as { error?: unknown }).error;
    setInfo(e ? errText(e) : r.data?.detail ?? null); load();
  };
  async function decide(d: Decision) {
    if (!sel) return;
    setBusy(true);
    const r = await api.POST("/api/approvals/message/{thread_id}", { params: { path: { thread_id: sel } }, body: d });
    setBusy(false);
    setInfo(r.error ? errText(r.error) : d.action === "reject" ? "Rejected — nothing sent." : "Approved — sending…");
    setTimeout(() => open(sel), 1500);
  }
  return (
    <>
      <div className="row"><h1>Messages</h1><span className="spacer" /><button onClick={sync}>Check for replies</button></div>
      {info && <div className="banner info" role="status">{info}</div>}
      <div className="grid two">
        <section className="card">
          {threads === null ? <div className="skeleton" /> : threads.length === 0 ? <p className="muted">No client conversations yet. Replies to submitted proposals appear here.</p> :
            <ul style={{ listStyle: "none", padding: 0 }}>{threads.map((t) => (
              <li key={t.thread_id}><button style={{ width: "100%", textAlign: "left", marginBottom: 6 }} aria-pressed={sel === t.thread_id} onClick={() => open(t.thread_id)}>
                <strong>{t.job_id ?? t.thread_id}</strong> <span className="badge">{t.intent ?? t.status}</span>
                <div className="small muted">{t.last_message}</div></button></li>))}</ul>}
        </section>
        <section className="grid">
          {sel && <div className="card"><h2>Thread</h2>{msgs.map((m) => (
            <p key={m.message_id} className="small"><strong>{m.sender}</strong> <span className="muted">{m.sent_at?.slice(0, 16)}</span><br />{m.body}</p>))}</div>}
          {pending && <>
            <div className="card small"><strong>Suggested reply</strong> · intent <span className="badge">{pending.intent?.intent}</span>
              {pending.intent?.injection_detected && <span className="badge bad"> injected instruction ignored</span>}
              {(pending.slots ?? []).length > 0 && <p>Proposed slots: {pending.slots.map((s: any) => s.start.slice(0, 16)).join(" · ")}</p>}
              <p>{pending.text}</p></div>
            <ApprovalPanel kind="message" text={pending.text} preview={pending.preview} onDecide={decide} busy={busy} /></>}
        </section>
      </div>
    </>
  );
}
