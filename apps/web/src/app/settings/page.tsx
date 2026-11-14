import { useEffect, useState } from "react";
import { api, errText, type MemoryItem, type Settings } from "@/lib/client";

export default function SettingsPage() {
  const [s, setS] = useState<Settings | null>(null);
  const [mem, setMem] = useState<MemoryItem[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [upwork, setUpwork] = useState<{ connected?: boolean; expires_at?: string | null } | null>(null);
  const load = async () => {
    const u = await api.GET("/api/auth/upwork/status"); setUpwork((u.data as any) ?? null);
    const r = await api.GET("/api/settings"); setS(r.data ?? null);
    const m = await api.GET("/api/memory"); setMem(m.data ?? []);
  };
  useEffect(() => { load(); }, []);
  if (!s) return <div className="skeleton" />;
  const save = async () => { const r = await api.PUT("/api/settings", { body: s }); setMsg(r.error ? errText(r.error) : "Saved."); };
  const del = async (m: MemoryItem) => {
    await api.DELETE("/api/memory/{namespace}/{key}", { params: { path: { namespace: m.namespace.join("."), key: m.key } } });
    load();
  };
  return (
    <>
      <h1>Settings</h1>
      {msg && <div className="banner info" role="status">{msg}</div>}
      <section className="card grid two">
        <label>Autonomy
          <select value={s.autonomy} onChange={(e) => setS({ ...s, autonomy: e.target.value as Settings["autonomy"] })}>
            <option value="suggest">Suggest only (I decide every job)</option>
            <option value="auto_skip_low_fit">Auto-skip low-fit jobs (never auto-send)</option></select></label>
        <label>Weekly Connects budget<input inputMode="numeric" value={s.weekly_connects_budget} onChange={(e) => setS({ ...s, weekly_connects_budget: Number(e.target.value) || 0 })} /></label>
        <label>Hourly rate (USD)<input inputMode="decimal" value={s.hourly_rate ?? ""} onChange={(e) => setS({ ...s, hourly_rate: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Minimum project (USD)<input inputMode="decimal" value={s.min_project ?? ""} onChange={(e) => setS({ ...s, min_project: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Marketplace
          <select value={s.marketplace} onChange={(e) => setS({ ...s, marketplace: e.target.value as Settings["marketplace"] })}>
            <option value="gigsim">GigSim</option><option value="upwork">Upwork (official MCP, OAuth)</option></select></label>
        <label>Saved search keywords<input value={s.saved_searches?.[0]?.query ?? ""} onChange={(e) => setS({ ...s, saved_searches: [{ ...(s.saved_searches?.[0] ?? { name: "Default", skills: [], interval_min: 30 }), query: e.target.value } as any] })} /></label>
        <div><button className="primary" onClick={save}>Save settings</button></div>
      </section>
      <section className="card" style={{ marginTop: 12 }}><h2>Connected accounts</h2>
        <div className="row">
          <span>Upwork (official MCP server, OAuth — GigBid only ever creates drafts you confirm on Upwork):</span>
          <span className={`badge ${upwork?.connected ? "ok" : ""}`}>{upwork?.connected ? "connected" : "not connected"}</span>
          {upwork?.connected
            ? <button onClick={async () => { await api.POST("/api/auth/upwork/disconnect"); load(); }}>Disconnect</button>
            : <button onClick={async () => {
                const r = await api.GET("/api/auth/upwork/start");
                const e = (r as { error?: unknown }).error;
                if (e || !(r.data as any)?.authorize_url) return setMsg(errText(e ?? "Upwork OAuth unavailable"));
                location.href = (r.data as any).authorize_url;
              }}>Connect Upwork</button>}
        </div>
      </section>
      <section className="card" style={{ marginTop: 12 }} data-testid="memory-viewer"><h2>What GigBid has learned</h2>
        <p className="small muted">Only knowledge promoted by an explicit rule appears here (style: same edit seen in 3+ drafts across 2+ jobs; outcome: reply-rate lift over 5+ proposals). Delete anything you disagree with.</p>
        {mem.length === 0 ? <p className="muted">Nothing learned yet.</p> :
          <table className="responsive"><thead><tr><th>Memory</th><th>Rule</th><th>Evidence</th><th></th></tr></thead>
            <tbody>{mem.map((m) => <tr key={m.namespace.join(".") + m.key}>
              <td data-label="Memory">{(m.data as any).rule ?? (m.data as any).insight}</td><td data-label="Rule"><code>{(m.data as any).promoted_by}</code></td>
              <td data-label="Evidence" className="small">{((m.data as any).evidence_jobs ?? []).join(", ") || `n=${(m.data as any).n}`}</td>
              <td><button className="danger small" onClick={() => del(m)}>Delete</button></td></tr>)}</tbody></table>}
      </section>
    </>
  );
}
