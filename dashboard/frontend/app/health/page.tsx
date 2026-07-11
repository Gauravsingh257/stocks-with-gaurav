"use client";

/**
 * /health — Product Health Dashboard (internal, admin-only).
 *
 * Reads our first-party event store (/api/product-analytics/*) to show the 16
 * validation KPIs + the activation funnel with stage-to-stage conversion. This
 * is the primary PMF-signal surface for the Sprint-1 validation window.
 */
import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw, TrendingDown, IndianRupee, Layers, HeartPulse, Zap, RotateCw, LogOut } from "lucide-react";
import { API_BASE } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ADMIN_EMAILS = new Set(["hellogaurav2577@gmail.com"]);

interface Health {
  window_days: number; since: string;
  total_users: number; active_users: number; new_signups: number; returning_users: number;
  command_center_views: number; nba_clicks: number; nba_ctr_pct: number;
  watchlist_adds: number; watchlist_opens: number; research_searches: number; ai_research_usage: number;
  avg_session_seconds: number; pages_per_session: number; telegram_link_clicks: number;
  day1_retention_pct: number | null; day1_cohort: number; day7_retention_pct: number | null;
  session_endings?: Record<string, number>;
}
interface FunnelStage { stage: string; count: number; conversion_pct: number; overall_pct: number; }
interface Funnel { window_days: number; since: string; stages: FunnelStage[]; note: string; }
interface FeatureRow {
  feature: string; event: string; unique_users: number; daily_uses: number; weekly_uses: number;
  avg_session_seconds: number; repeat_usage_pct: number; adoption_pct: number;
}
interface Adoption { window_days: number; active_users: number; features: FeatureRow[]; }
interface Business {
  registered_users: number; premium_accounts: number; free_users: number; admin_users: number;
  subscription_price_inr: number; mrr_estimate_inr: number; arpu_estimate_inr: number; estimate_note: string;
  coming_soon: Record<string, string>;
}

function fmtDuration(s: number): string {
  if (!s || s <= 0) return "0s";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function fmtINR(n: number): string {
  if (n >= 10000000) return `₹${(n / 10000000).toFixed(2)}Cr`;
  if (n >= 100000) return `₹${(n / 100000).toFixed(2)}L`;
  if (n >= 1000) return `₹${(n / 1000).toFixed(1)}k`;
  return `₹${n}`;
}

export default function HealthPage() {
  const { user, token, loading: authLoading } = useAuth();
  const [days, setDays] = useState(7);
  const [health, setHealth] = useState<Health | null>(null);
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [adoption, setAdoption] = useState<Adoption | null>(null);
  const [business, setBusiness] = useState<Business | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const isAdmin = !!user && (user.role === "ADMIN" || ADMIN_EMAILS.has((user.email || "").toLowerCase()));

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true); setErr(null);
    try {
      const h: Record<string, string> = { Authorization: `Bearer ${token}` };
      const [hr, fr, ar, br] = await Promise.all([
        fetch(`${API_BASE}/api/product-analytics/health?days=${days}`, { headers: h, cache: "no-store" }),
        fetch(`${API_BASE}/api/product-analytics/funnel?days=${days}`, { headers: h, cache: "no-store" }),
        fetch(`${API_BASE}/api/product-analytics/adoption?days=${days}`, { headers: h, cache: "no-store" }),
        fetch(`${API_BASE}/api/product-analytics/business`, { headers: h, cache: "no-store" }),
      ]);
      if (hr.status === 403 || fr.status === 403) { setErr("Admin access required."); return; }
      if (!hr.ok || !fr.ok) { setErr("Could not load product health."); return; }
      setHealth(await hr.json());
      setFunnel(await fr.json());
      if (ar.ok) setAdoption(await ar.json());
      if (br.ok) setBusiness(await br.json());
    } catch {
      setErr("Could not load product health.");
    } finally {
      setLoading(false);
    }
  }, [token, days]);

  useEffect(() => { if (isAdmin) load(); else setLoading(false); }, [isAdmin, load]);

  if (authLoading) return <Centered>Loading…</Centered>;
  if (!user) return <Centered>Sign in required.</Centered>;
  if (!isAdmin) return <Centered>This dashboard is internal (admin only).</Centered>;

  const kpis: { label: string; value: string; sub?: string; accent?: boolean }[] = health ? [
    { label: "Total Users", value: String(health.total_users), sub: "unique, all-time" },
    { label: "Active Users", value: String(health.active_users), sub: `last ${health.window_days}d` },
    { label: "New Signups", value: String(health.new_signups) },
    { label: "Returning Users", value: String(health.returning_users) },
    { label: "Command Center Views", value: String(health.command_center_views) },
    { label: "NBA Clicks", value: String(health.nba_clicks) },
    { label: "NBA CTR", value: `${health.nba_ctr_pct}%`, sub: "clicks / CC views", accent: true },
    { label: "Watchlist Adds", value: String(health.watchlist_adds) },
    { label: "Watchlist Opens", value: String(health.watchlist_opens) },
    { label: "Research Searches", value: String(health.research_searches) },
    { label: "AI Research Usage", value: String(health.ai_research_usage), sub: "not wired yet" },
    { label: "Avg Session", value: fmtDuration(health.avg_session_seconds) },
    { label: "Pages / Session", value: String(health.pages_per_session) },
    { label: "Telegram Clicks", value: String(health.telegram_link_clicks), sub: "no CTA yet" },
    { label: "Day-1 Retention", value: health.day1_retention_pct == null ? "—" : `${health.day1_retention_pct}%`, sub: `cohort ${health.day1_cohort}`, accent: true },
    { label: "Day-7 Retention", value: health.day7_retention_pct == null ? "— soon" : `${health.day7_retention_pct}%`, sub: "needs 7d data" },
  ] : [];

  const maxCount = funnel ? Math.max(1, ...funnel.stages.map((s) => s.count)) : 1;

  return (
    <div className="w-full max-w-screen-xl mx-auto flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          <Activity size={22} color="var(--accent)" />
          <div>
            <h1 className="text-lg md:text-xl font-extrabold m-0" style={{ color: "var(--text-primary)" }}>Product Health</h1>
            <p className="m-0" style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>
              First-party KPIs · Sprint-1 validation {health?.since ? `· since ${health.since}` : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {[1, 7, 30].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className="badge" style={{ cursor: "pointer", padding: "5px 11px", fontSize: "0.72rem",
                background: days === d ? "var(--accent-dim)" : "transparent",
                border: `1px solid ${days === d ? "var(--accent)" : "var(--border)"}`,
                color: days === d ? "var(--accent)" : "var(--text-secondary)" }}>
              {d}d
            </button>
          ))}
          <button onClick={load} className="btn-accent" style={{ padding: "5px 12px", fontSize: "0.72rem" }}>
            <RefreshCw size={12} style={{ display: "inline", marginRight: 4, verticalAlign: "middle" }} /> Refresh
          </button>
        </div>
      </div>

      {/* Founder summary — the four questions this page must answer at a glance */}
      {health && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <FounderCard icon={HeartPulse} q="Is the product healthy?"
            value={`${health.active_users} active`} sub={`last ${health.window_days}d`}
            tone={health.active_users > 0 ? "good" : "dim"} />
          <FounderCard icon={Zap} q="Are users engaged?"
            value={`${health.nba_ctr_pct}% NBA CTR`} sub={`${health.pages_per_session} pages/session`}
            tone={health.nba_ctr_pct >= 15 ? "good" : health.nba_ctr_pct > 0 ? "warn" : "dim"} />
          <FounderCard icon={RotateCw} q="Are users returning?"
            value={health.day1_retention_pct == null ? "—" : `${health.day1_retention_pct}% D1`} sub={`cohort ${health.day1_cohort}`}
            tone={(health.day1_retention_pct ?? 0) >= 25 ? "good" : (health.day1_retention_pct ?? 0) > 0 ? "warn" : "dim"} />
          <FounderCard icon={IndianRupee} q="Are subscriptions growing?"
            value={business ? `${business.premium_accounts} paid` : "—"} sub={business ? `${fmtINR(business.mrr_estimate_inr)} MRR est` : ""}
            tone={(business?.premium_accounts ?? 0) > 0 ? "good" : "dim"} />
        </div>
      )}

      {err && (
        <div className="glass rounded-xl" style={{ padding: "14px 16px", border: "1px solid rgba(251,113,133,0.3)", color: "var(--danger)", fontSize: "0.82rem" }}>{err}</div>
      )}
      {loading && !health && <Centered>Loading product health…</Centered>}

      {/* KPI grid */}
      {health && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {kpis.map((k) => (
            <div key={k.label} className="glass rounded-xl" style={{ padding: "14px 16px" }}>
              <div style={{ fontSize: "0.62rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-dim)" }}>{k.label}</div>
              <div style={{ fontSize: "1.35rem", fontWeight: 800, marginTop: 4, color: k.accent ? "var(--accent)" : "var(--text-primary)" }}>{k.value}</div>
              {k.sub && <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", marginTop: 2 }}>{k.sub}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Activation funnel */}
      {funnel && (
        <div className="glass rounded-xl" style={{ padding: "16px 18px" }}>
          <div className="flex items-center gap-2 mb-1">
            <TrendingDown size={15} color="var(--accent)" />
            <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--text-primary)" }}>Activation Funnel</span>
          </div>
          <p style={{ fontSize: "0.68rem", color: "var(--text-dim)", margin: "0 0 14px" }}>{funnel.note}</p>
          <div className="flex flex-col gap-2">
            {funnel.stages.map((s, i) => (
              <div key={s.stage} className="flex items-center gap-3">
                <div style={{ width: 130, fontSize: "0.78rem", color: "var(--text-secondary)", flexShrink: 0 }}>{s.stage}</div>
                <div style={{ flex: 1, position: "relative", height: 30, background: "rgba(255,255,255,0.03)", borderRadius: 7, overflow: "hidden" }}>
                  <div style={{ position: "absolute", inset: 0, width: `${Math.max(2, (s.count / maxCount) * 100)}%`,
                    background: "linear-gradient(90deg, color-mix(in srgb, var(--accent) 45%, transparent), color-mix(in srgb, var(--accent) 20%, transparent))",
                    borderRadius: 7 }} />
                  <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 10px" }}>
                    <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--text-primary)" }}>{s.count}</span>
                    <span style={{ fontSize: "0.66rem", color: "var(--text-dim)" }}>{s.overall_pct}% of visitors</span>
                  </div>
                </div>
                <div style={{ width: 58, textAlign: "right", fontSize: "0.72rem", flexShrink: 0,
                  color: i === 0 ? "var(--text-dim)" : s.conversion_pct >= 50 ? "var(--success)" : s.conversion_pct >= 20 ? "var(--warning)" : "var(--danger)" }}>
                  {i === 0 ? "—" : `${s.conversion_pct}%`}
                </div>
              </div>
            ))}
          </div>
          <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "12px 0 0" }}>
            Right column = conversion from the stage above. The most important product KPI.
          </p>
        </div>
      )}

      {/* Business Health */}
      {business && (
        <div className="glass rounded-xl" style={{ padding: "16px 18px" }}>
          <div className="flex items-center gap-2 mb-1">
            <IndianRupee size={15} color="var(--accent)" />
            <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--text-primary)" }}>Business Health</span>
          </div>
          <p style={{ fontSize: "0.68rem", color: "var(--text-dim)", margin: "0 0 14px" }}>{business.estimate_note}</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MiniStat label="MRR (est.)" value={fmtINR(business.mrr_estimate_inr)} sub={`${business.premium_accounts} × ${fmtINR(business.subscription_price_inr)}`} accent />
            <MiniStat label="Paid / Premium" value={String(business.premium_accounts)} sub="role-based (real)" />
            <MiniStat label="Registered Users" value={String(business.registered_users)} sub={`${business.free_users} free`} />
            <MiniStat label="ARPU (est.)" value={fmtINR(business.arpu_estimate_inr)} sub="MRR / registered" />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
            {Object.entries(business.coming_soon).map(([k, v]) => (
              <div key={k} className="rounded-xl" style={{ padding: "12px 14px", border: "1px dashed var(--border)", opacity: 0.7 }}>
                <div style={{ fontSize: "0.6rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-dim)" }}>{k.replace(/_/g, " ").replace(/pct/, "%")}</div>
                <div style={{ fontSize: "0.82rem", fontWeight: 700, marginTop: 4, color: "var(--text-dim)" }}>{v}</div>
              </div>
            ))}
          </div>
          <p style={{ fontSize: "0.62rem", color: "var(--text-dim)", margin: "12px 0 0" }}>
            Dashed cards unlock when a billing system (Razorpay) ships — MRR/ARPU become real, and churn/renewal/refund/LTV/CAC become measurable.
          </p>
        </div>
      )}

      {/* Feature Adoption */}
      {adoption && (
        <div className="glass rounded-xl" style={{ padding: "16px 18px" }}>
          <div className="flex items-center gap-2 mb-1">
            <Layers size={15} color="var(--accent)" />
            <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--text-primary)" }}>Feature Adoption</span>
          </div>
          <p style={{ fontSize: "0.68rem", color: "var(--text-dim)", margin: "0 0 12px" }}>
            Sorted by adoption. {adoption.active_users} active users in window. Use this to prioritise the roadmap.
          </p>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem", minWidth: 640 }}>
              <thead>
                <tr style={{ color: "var(--text-dim)", fontSize: "0.64rem", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>Feature</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Unique</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Daily</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Weekly</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Avg time</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Repeat</th>
                  <th style={{ textAlign: "left", padding: "6px 8px", width: 150 }}>Adoption</th>
                </tr>
              </thead>
              <tbody>
                {adoption.features.map((f) => (
                  <tr key={f.event} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "8px", fontWeight: 600, color: "var(--text-primary)" }}>{f.feature}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{f.unique_users}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{f.daily_uses}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{f.weekly_uses}</td>
                    <td style={{ padding: "8px", textAlign: "right", color: "var(--text-secondary)" }}>{fmtDuration(f.avg_session_seconds)}</td>
                    <td style={{ padding: "8px", textAlign: "right", color: "var(--text-secondary)" }}>{f.repeat_usage_pct}%</td>
                    <td style={{ padding: "8px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ flex: 1, height: 8, background: "rgba(255,255,255,0.05)", borderRadius: 4, overflow: "hidden" }}>
                          <div style={{ width: `${Math.min(100, f.adoption_pct)}%`, height: "100%", background: "var(--accent)", borderRadius: 4 }} />
                        </div>
                        <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--accent)", minWidth: 38, textAlign: "right" }}>{f.adoption_pct}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Session endings (churn signal) */}
      {health && health.session_endings && Object.keys(health.session_endings).length > 0 && (
        <div className="glass rounded-xl" style={{ padding: "16px 18px" }}>
          <div className="flex items-center gap-2 mb-3">
            <LogOut size={15} color="var(--accent)" />
            <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--text-primary)" }}>Session Endings</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(health.session_endings).map(([reason, count]) => (
              <div key={reason} className="rounded-lg" style={{ padding: "8px 12px", background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)" }}>
                <span style={{ fontSize: "0.82rem", fontWeight: 700, color: "var(--text-primary)" }}>{count}</span>
                <span style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginLeft: 6 }}>{reason.replace(/_/g, " ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function FounderCard({ icon: Icon, q, value, sub, tone }: { icon: typeof HeartPulse; q: string; value: string; sub?: string; tone: "good" | "warn" | "dim" }) {
  const color = tone === "good" ? "var(--success)" : tone === "warn" ? "var(--warning)" : "var(--text-secondary)";
  return (
    <div className="glass rounded-xl" style={{ padding: "14px 16px", borderLeft: `3px solid ${color}` }}>
      <div className="flex items-center gap-1.5" style={{ color: "var(--text-dim)" }}>
        <Icon size={13} />
        <span style={{ fontSize: "0.66rem" }}>{q}</span>
      </div>
      <div style={{ fontSize: "1.2rem", fontWeight: 800, marginTop: 6, color }}>{value}</div>
      {sub && <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function MiniStat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div className="rounded-xl" style={{ padding: "12px 14px", background: "rgba(255,255,255,0.02)", border: "1px solid var(--border)" }}>
      <div style={{ fontSize: "0.6rem", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-dim)" }}>{label}</div>
      <div style={{ fontSize: "1.05rem", fontWeight: 800, marginTop: 4, color: accent ? "var(--accent)" : "var(--text-primary)" }}>{value}</div>
      {sub && <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div style={{ textAlign: "center", padding: 80, color: "var(--text-secondary)", fontSize: "0.9rem" }}>{children}</div>;
}
