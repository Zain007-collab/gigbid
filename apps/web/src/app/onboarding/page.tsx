import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errText } from "@/lib/client";
import { useMe } from "@/components/Layout";

const PERSONAS = [
  { id: "ai_ml", label: "AI/ML engineer", hint: "RAG, LLM agents, ML pipelines" },
  { id: "fullstack", label: "Full-stack web developer", hint: "React/Next.js, Node, Python backends" },
  { id: "data_analyst", label: "Data analyst", hint: "SQL, dbt, BI dashboards, experiments" },
] as const;

export default function Onboarding() {
  const nav = useNavigate();
  const { refresh } = useMe();
  const [persona, setPersona] = useState<(typeof PERSONAS)[number]["id"]>("ai_ml");
  const [marketplace, setMarketplace] = useState<"gigsim" | "upwork">("gigsim");
  const [rate, setRate] = useState("");
  const [minProject, setMinProject] = useState("");
  const [budget, setBudget] = useState("80");
  const [query, setQuery] = useState("");
  const [caseStudy, setCaseStudy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const r = await api.POST("/api/auth/onboarding", { body: { persona, settings: {
      marketplace, hourly_rate: rate ? Number(rate) : null, min_project: minProject ? Number(minProject) : null,
      weekly_connects_budget: Number(budget), autonomy: "suggest", timezone: "Asia/Karachi",
      saved_searches: [{ name: "Default", query, skills: [], min_budget: null, interval_min: 30 }] } } });
    if (r.error) return setErr(errText(r.error));
    if (caseStudy.trim().length > 50) await fetch("/api/portfolio/upload", { method: "POST", body: caseStudy, credentials: "include" });
    await refresh();
    nav("/inbox");
  }
  return (
    <form onSubmit={save} className="grid" style={{ maxWidth: 720 }} aria-label="Onboarding">
      <h1>Set up GigBid</h1>
      <fieldset className="card grid"><legend>1. Profile</legend>
        {PERSONAS.map((p) => (
          <label key={p.id} className="row" style={{ display: "flex" }}>
            <input type="radio" name="persona" style={{ width: "auto" }} checked={persona === p.id} onChange={() => setPersona(p.id)} />
            <span><strong>{p.label}</strong> <span className="muted small">— {p.hint} (synthetic demo portfolio)</span></span>
          </label>))}
      </fieldset>
      <fieldset className="card grid"><legend>2. Marketplace</legend>
        <label><span>Connect to</span>
          <select value={marketplace} onChange={(e) => setMarketplace(e.target.value as "gigsim" | "upwork")}>
            <option value="gigsim">GigSim (simulated marketplace)</option>
            <option value="upwork">Upwork (official MCP server, OAuth — drafts only)</option>
          </select></label>
        {marketplace === "upwork" && <p className="small muted">Live mode reads jobs through Upwork's official MCP server and only ever creates drafts you confirm on Upwork. Connect your account from Settings after setup.</p>}
      </fieldset>
      <fieldset className="card grid two"><legend>3. Rates and budget</legend>
        <label>Hourly rate (USD)<input inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="from profile" /></label>
        <label>Minimum project (USD)<input inputMode="decimal" value={minProject} onChange={(e) => setMinProject(e.target.value)} placeholder="from profile" /></label>
        <label>Weekly Connects budget<input inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
        <label>Saved search (keywords)<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. RAG chatbot" /></label>
      </fieldset>
      <fieldset className="card grid"><legend>4. Portfolio (optional)</legend>
        <label>Paste a case study (markdown) — it is indexed immediately
          <textarea rows={5} value={caseStudy} onChange={(e) => setCaseStudy(e.target.value)} /></label>
      </fieldset>
      {err && <div className="banner bad" role="alert">{err}</div>}
      <button className="primary" data-testid="finish-onboarding">Finish setup</button>
    </form>
  );
}
