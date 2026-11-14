import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { api, type Health, type Me } from "@/lib/client";

const MeCtx = createContext<{ me: Me | null; refresh: () => Promise<void> }>({ me: null, refresh: async () => {} });
export const useMe = () => useContext(MeCtx);

export function DegradedBanner() {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const { data } = await api.GET("/api/health");
      if (alive && data) setH(data);
    };
    tick();
    const t = setInterval(tick, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!h || h.marketplace_up) return null;
  return (
    <div className="banner bad" role="alert" data-testid="degraded-banner">
      <strong>Marketplace unavailable.</strong> {h.detail ?? "New work is queued and will resume automatically."}
    </div>
  );
}

export default function Layout() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const loc = useLocation();
  const refresh = async () => {
    const { data } = await api.GET("/api/auth/me");
    setMe(data ?? null);
  };
  useEffect(() => { refresh(); }, []);
  if (me === undefined) return <main><div className="skeleton" /><div className="skeleton" /></main>;
  if (me === null) return <Navigate to="/login" replace />;
  if (!me.onboarded && loc.pathname !== "/onboarding") return <Navigate to="/onboarding" replace />;
  const links: [string, string][] = [["/inbox", "Job inbox"], ["/pipeline", "Pipeline"], ["/messages", "Messages"],
    ["/analytics", "Analytics"], ["/settings", "Settings"]];
  return (
    <MeCtx.Provider value={{ me, refresh }}>
      <div className="shell">
        <nav className="side" aria-label="Main">
          <div className="brand">GigBid</div>
          {links.map(([to, label]) => <NavLink key={to} to={to}>{label}</NavLink>)}
          <div className="small muted" style={{ marginTop: 16 }}>{me.email}<br />persona: {me.persona ?? "—"}</div>
          <button className="small" style={{ marginTop: 8 }} onClick={async () => { await api.POST("/api/auth/logout"); location.href = "/login"; }}>Sign out</button>
        </nav>
        <main>
          <DegradedBanner />
          <Outlet />
        </main>
      </div>
    </MeCtx.Provider>
  );
}
