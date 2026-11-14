import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errText } from "@/lib/client";

export default function Login() {
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const r = mode === "login"
      ? await api.POST("/api/auth/login", { body: { email, password } })
      : await api.POST("/api/auth/register", { body: { email, password } });
    setBusy(false);
    if (r.error) return setErr(errText(r.error));
    nav(r.data?.onboarded ? "/inbox" : "/onboarding");
  }
  return (
    <main style={{ maxWidth: 420, margin: "8vh auto" }}>
      <h1>GigBid</h1>
      <p className="muted">Finds freelance jobs worth bidding on and drafts proposals grounded in your portfolio. Nothing is sent without your approval.</p>
      <form className="card grid" onSubmit={submit} aria-label={mode === "login" ? "Sign in" : "Create account"}>
        <label>Email<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></label>
        <label>Password<input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
        {err && <div className="banner bad" role="alert">{err}</div>}
        <button className="primary" disabled={busy}>{busy ? "…" : mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}</button>
      </form>
    </main>
  );
}
