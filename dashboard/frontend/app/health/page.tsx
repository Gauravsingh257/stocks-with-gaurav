"use client";

/**
 * /health — Product Health Dashboard (internal, admin-only).
 *
 * Reads our first-party event store (/api/product-analytics/*).
 *
 * The page keeps six entities strictly apart — ACCOUNTS, USERS, BROWSERS,
 * TAB SESSIONS, EVENTS and ENGAGEMENT — because conflating them is exactly how
 * this dashboard previously reported 228 "users" when 12 accounts existed.
 * Anything derived from a browser id is labelled "browsers (approx.)" and is
 * never called a user.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Activity, RefreshCw, TrendingDown, Layers, HeartPulse, Zap, RotateCw, LogOut,
  Users, Monitor, Info, Building2,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ADMIN_EMAILS = new Set(["hellogaurav2577@gmail.com"]);

interface Basis {
  window_days: number; since: string;
  unique_user_data_from: string; accounts_data_from: string | null;
  internal_accounts_excluded: number; correction_note: string;
  entities: Record<string, string>;
}
interface Health extends Basis {
  include_internal: boolean;
  accounts: { registered_total: number; by_role: Record<string, number>; new_today: number; new_7d: number; new_30d: number };
  users: {
    dau: number; wau: number; mau: number;
    engaged_dau: number; engaged_wau: number; engaged_mau: number;
    active_in_window: number; returning: number; ever_authenticated: number;
  };
  browsers: { anonymous_in_window: number; distinct_all_time: number; note: string };
  tab_sessions: number; events: number;
  engagement_events: Record<string, number>;
  nba_ctr_pct: number; avg_tab_session_seconds: number; pages_per_tab_session: number;
  session_endings?: Record<string, number>;
  day1_retention_pct: number | null; day1_cohort: number; day1_unavailable_reason: string | null;
  day7_retention_pct: number | null; day7_unavailable_reason: string | null;
  auth_signals: {
    login_events_client_reported: number; signup_events_client_reported: number;
    new_accounts_db_truth: number | null; failed_logins: number | null; note: string;
  };
  verified_users: number | null; verified_users_unavailable_reason: string;
}
interface FunnelStage { stage: string; count: number; entity: string; conversion_pct: number; of_base_pct: number; }
interface Funnel extends Basis { anonymous_phase: FunnelStage[]; authenticated_phase: FunnelStage[]; note: string; }
interface FeatureRow {
  feature: string; event: string; unique_users: number; browsers: number; uses: number; adoption_pct: number | null;
}
interface Adoption extends Basis { active_users: number; features: FeatureRow[]; note: string }
interface Business {
  registered_users: number; accounts_by_role: Record<string, number>;
  new_accounts_today: number; new_accounts_7d: number; new_accounts_30d: number;
  accounts_data_from: string | null;
  billing: { status: string; note: string }; role_note: string;
}

function fmtDuration(s: number): string {
  if (!s || s <= 0) return "0s";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

export default function HealthPage() {
  const { user, token, loading: authLoading } = useAuth();
  const [days, setDays] = useState(7);
  const [includeInternal, setIncludeInternal] = useState(false);
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
      const q = `days=${days}&include_internal=${includeInternal}`;
      const [hr, fr, ar, br] = await Promise.all([
        fetch(`${API_BASE}/api/product-analytics/health?${q}`, { headers: h, cache: "no-store" }),
        fetch(`${API_BASE}/api/product-analytics/funnel?${q}`, { headers: h, cache: "no-store" }),
        fetch(`${API_BASE}/api/product-analytics/adoption?${q}`, { headers: h, cache: "no-store" }),
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
  }, [token, days, includeInternal]);

  useEffect(() => { if (isAdmin) load(); else setLoading(false); }, [isAdmin, load]);

  if (authLoading) return <Centered>Loading…</Centered>;
  if (!user) return <Centered>Sign in required.</Centered>;
  if (!isAdmin) return <Centered>This dashboard is internal (admin only).</Centered>;

  const windowLabel = `last ${health?.window_days ?? days}d`;

  return (
    <div className="w-full max-w-screen-xl mx-auto flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          <Activity size={22} color="var(--accent)" />
          <div>
            <h1 className="text-lg md:text-xl font-extrabold m-0" style={{ color: "var(--text-primary)" }}>Product Health</h1>
            <p className="m-0" style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>
              First-party analytics {health?.since ? `· since ${health.since}` : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {[1, 7, 30].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className="badge" style={{ cursor: "pointer", padding: "5px 11px", fontSize: "0.72rem",
                background: days === d ? "var(--accent-dim)" : "transparent",
                border: `1px solid ${days === d ? "var(--accent)" : "var(--border)"}`,
                color: days === d ? "var(--accent)" : "var(--text-secondary)" }}>
              {d}d
            </button>
          ))}
          <button
            onClick={() => setIncludeInternal((v) => !v)}
            title="Add our own accounts back in. For reconciling a number against the raw table — not for reporting."
            className="badge"
            style={{ cursor: "pointer", padding: "5px 11px", fontSize: "0.72rem",
              background: includeInternal ? "var(--warning)" : "transparent",
              border: `1px solid ${includeInternal ? "var(--warning)" : "var(--border)"}`,
              color: includeInternal ? "#1a1205" : "var(--text-secondary)" }}>
            {includeInternal ? "incl. internal" : "excl. internal"}
          </button>
          <button onClick={load} className="btn-accent" style={{ padding: "5px 12px", fontSize: "0.72rem" }}>
            <RefreshCw size={12} style={{ display: "inline", marginRight: 4, verticalAlign: "middle" }} /> Refresh
          </button>
        </div>
      </div>

      {/* How these numbers are counted — the correction, stated plainly. */}
      {health && (
        <div className="rounded-xl" style={{ padding: "12px 15px", border: "1px solid var(--border)", background: "rgba(255,255,255,0.02)" }}>
          <div className="flex items-start gap-2">
            <Info size={14} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 2 }} />
            <div>
              <div style={{ fontSize: "0.74rem", fontWeight: 700, color: "var(--text-primary)" }}>How these numbers are counted</div>
              <p style={{ fontSize: "0.68rem", color: "var(--text-secondary)", margin: "4px 0 0", lineHeight: 1.55 }}>
                {health.correction_note}
              </p>
              <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "6px 0 0" }}>
                Accounts since {health.accounts_data_from?.slice(0, 10) ?? "—"} · unique-user analytics since {health.unique_user_data_from}
                {health.internal_accounts_excluded > 0 && !health.include_internal
                  ? ` · ${health.internal_accounts_excluded} internal account(s) excluded`
                  : health.include_internal ? " · internal accounts INCLUDED (debug)" : ""}
              </p>
            </div>
          </div>
        </div>
      )}

      {err && (
        <div className="glass rounded-xl" style={{ padding: "14px 16px", border: "1px solid rgba(251,113,133,0.3)", color: "var(--danger)", fontSize: "0.82rem" }}>{err}</div>
      )}
      {loading && !health && <Centered>Loading product health…</Centered>}

      {/* The four questions, each answered with ONE entity */}
      {health && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <FounderCard icon={Users} q="How many people signed up?"
            value={`${health.accounts.registered_total} accounts`} sub={`${health.accounts.new_30d} new in 30d`}
            tone={health.accounts.registered_total > 0 ? "good" : "dim"} />
          <FounderCard icon={HeartPulse} q="How many came back signed in?"
            value={`${health.users.wau} users`} sub={`WAU · ${health.users.engaged_wau} used a feature`}
            tone={health.users.wau > 0 ? "good" : "dim"} />
          <FounderCard icon={Zap} q="Are they engaging?"
            value={`${health.nba_ctr_pct}% NBA CTR`} sub={`${health.pages_per_tab_session} pages / tab session`}
            tone={health.nba_ctr_pct >= 15 ? "good" : health.nba_ctr_pct > 0 ? "warn" : "dim"} />
          <FounderCard icon={Monitor} q="How much anonymous traffic?"
            value={`${health.browsers.anonymous_in_window} browsers`} sub={`${windowLabel} · approx., not people`}
            tone="dim" />
        </div>
      )}

      {/* ACCOUNTS */}
      {health && (
        <Section icon={Users} title="Accounts" caption="People who registered. Source: the users table — not events.">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Registered total" value={String(health.accounts.registered_total)} accent />
            <Stat label="New today" value={String(health.accounts.new_today)} />
            <Stat label="New · 7d" value={String(health.accounts.new_7d)} />
            <Stat label="New · 30d" value={String(health.accounts.new_30d)} />
            <Stat label="Verified users" value="—"
              sub={health.verified_users_unavailable_reason} />
            <Stat label="Ever signed in" value={String(health.users.ever_authenticated)} sub="accounts seen in events" />
            {Object.entries(health.accounts.by_role).map(([role, n]) => (
              <Stat key={role} label={`Role · ${role}`} value={String(n)} />
            ))}
          </div>
        </Section>
      )}

      {/* AUTHENTICATED USERS */}
      {health && (
        <Section icon={HeartPulse} title="Authenticated users"
          caption="DISTINCT signed-in people. One person is one user no matter how many logins, tabs or devices.">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="DAU" value={String(health.users.dau)} accent sub={`engaged ${health.users.engaged_dau}`} />
            <Stat label="WAU" value={String(health.users.wau)} accent sub={`engaged ${health.users.engaged_wau}`} />
            <Stat label="MAU" value={String(health.users.mau)} accent sub={`engaged ${health.users.engaged_mau}`} />
            <Stat label="Returning" value={String(health.users.returning)} sub="active now + seen before" />
            <Stat label="Day-1 retention"
              value={health.day1_retention_pct == null ? "—" : `${health.day1_retention_pct}%`}
              sub={health.day1_unavailable_reason ?? `cohort ${health.day1_cohort}`} />
            <Stat label="Day-7 retention" value="—" sub={health.day7_unavailable_reason ?? ""} />
            <Stat label="Logins (reported)" value={String(health.auth_signals.login_events_client_reported)} sub="browser-sent, under-counts" />
            <Stat label="Failed logins" value="—" sub="not recorded server-side yet" />
          </div>
          <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "10px 0 0" }}>{health.auth_signals.note}</p>
        </Section>
      )}

      {/* ANONYMOUS TRAFFIC + VOLUME */}
      {health && (
        <Section icon={Monitor} title="Anonymous traffic & volume"
          caption="Browser ids and raw counts. Useful for reach — but a browser is not a person.">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label={`Anonymous browsers · ${windowLabel}`} value={String(health.browsers.anonymous_in_window)} sub="approx." />
            <Stat label="Distinct browsers all-time" value={String(health.browsers.distinct_all_time)} sub="approx." />
            <Stat label="Tab sessions" value={String(health.tab_sessions)} sub="one browser TAB, not a login" />
            <Stat label="Events" value={String(health.events)} sub={windowLabel} />
            <Stat label="Avg tab session" value={fmtDuration(health.avg_tab_session_seconds)} />
            <Stat label="Pages / tab session" value={String(health.pages_per_tab_session)} />
          </div>
          <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "10px 0 0" }}>{health.browsers.note}</p>
        </Section>
      )}

      {/* ENGAGEMENT EVENT COUNTS */}
      {health && (
        <Section icon={Zap} title="Engagement" caption="Event counts — how often something happened, not how many people.">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(health.engagement_events).map(([k, v]) => (
              <Stat key={k} label={k.replace(/_/g, " ")} value={String(v)} />
            ))}
            <Stat label="NBA CTR" value={`${health.nba_ctr_pct}%`} sub="clicks / CC views" accent />
          </div>
        </Section>
      )}

      {/* Activation funnel — two phases, because the entity changes */}
      {funnel && (
        <Section icon={TrendingDown} title="Activation funnel" caption={funnel.note}>
          <FunnelPhase title="Anonymous phase — browsers (approx.)" stages={funnel.anonymous_phase} />
          <div style={{ height: 14 }} />
          <FunnelPhase title="Authenticated phase — signed-in users" stages={funnel.authenticated_phase} />
        </Section>
      )}

      {/* Feature adoption */}
      {adoption && (
        <Section icon={Layers} title="Feature adoption" caption={adoption.note}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem", minWidth: 620 }}>
              <thead>
                <tr style={{ color: "var(--text-dim)", fontSize: "0.64rem", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>Feature</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Users</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Browsers</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>Uses</th>
                  <th style={{ textAlign: "left", padding: "6px 8px", width: 170 }}>Adoption (of {adoption.active_users} users)</th>
                </tr>
              </thead>
              <tbody>
                {adoption.features.map((f) => (
                  <tr key={f.event} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "8px", fontWeight: 600, color: "var(--text-primary)" }}>{f.feature}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{f.unique_users}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: "var(--text-dim)" }}>{f.browsers}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: "var(--text-secondary)" }}>{f.uses}</td>
                    <td style={{ padding: "8px" }}>
                      {f.adoption_pct == null ? (
                        <span style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>— no active users</span>
                      ) : (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ flex: 1, height: 8, background: "rgba(255,255,255,0.05)", borderRadius: 4, overflow: "hidden" }}>
                            <div style={{ width: `${Math.min(100, f.adoption_pct)}%`, height: "100%", background: "var(--accent)", borderRadius: 4 }} />
                          </div>
                          <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--accent)", minWidth: 38, textAlign: "right" }}>{f.adoption_pct}%</span>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {/* Accounts & billing */}
      {business && (
        <Section icon={Building2} title="Accounts & billing" caption={business.billing.note}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Registered users" value={String(business.registered_users)} accent />
            <Stat label="New · today" value={String(business.new_accounts_today)} />
            <Stat label="New · 7d" value={String(business.new_accounts_7d)} />
            <Stat label="New · 30d" value={String(business.new_accounts_30d)} />
          </div>
          <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "12px 0 0" }}>{business.role_note}</p>
        </Section>
      )}

      {/* Session endings (churn signal) */}
      {health && health.session_endings && Object.keys(health.session_endings).length > 0 && (
        <Section icon={LogOut} title="Tab-session endings" caption="Why browser tabs ended — an event count.">
          <div className="flex flex-wrap gap-2">
            {Object.entries(health.session_endings).map(([reason, count]) => (
              <div key={reason} className="rounded-lg" style={{ padding: "8px 12px", background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)" }}>
                <span style={{ fontSize: "0.82rem", fontWeight: 700, color: "var(--text-primary)" }}>{count}</span>
                <span style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginLeft: 6 }}>{reason.replace(/_/g, " ")}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function FunnelPhase({ title, stages }: { title: string; stages: FunnelStage[] }) {
  const max = Math.max(1, ...stages.map((s) => s.count));
  return (
    <div>
      <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8 }}>{title}</div>
      <div className="flex flex-col gap-2">
        {stages.map((s, i) => (
          <div key={s.stage} className="flex items-center gap-3">
            <div style={{ width: 170, fontSize: "0.78rem", color: "var(--text-secondary)", flexShrink: 0 }}>{s.stage}</div>
            <div style={{ flex: 1, position: "relative", height: 30, background: "rgba(255,255,255,0.03)", borderRadius: 7, overflow: "hidden" }}>
              <div style={{ position: "absolute", inset: 0, width: `${Math.max(2, (s.count / max) * 100)}%`,
                background: "linear-gradient(90deg, color-mix(in srgb, var(--accent) 45%, transparent), color-mix(in srgb, var(--accent) 20%, transparent))",
                borderRadius: 7 }} />
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 10px" }}>
                <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--text-primary)" }}>{s.count}</span>
                <span style={{ fontSize: "0.66rem", color: "var(--text-dim)" }}>{s.entity}</span>
              </div>
            </div>
            <div style={{ width: 58, textAlign: "right", fontSize: "0.72rem", flexShrink: 0,
              color: i === 0 ? "var(--text-dim)" : s.conversion_pct >= 50 ? "var(--success)" : s.conversion_pct >= 20 ? "var(--warning)" : "var(--danger)" }}>
              {i === 0 ? "—" : `${s.conversion_pct}%`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Section({ icon: Icon, title, caption, children }: {
  icon: typeof HeartPulse; title: string; caption?: string; children: React.ReactNode;
}) {
  return (
    <div className="glass rounded-xl" style={{ padding: "16px 18px" }}>
      <div className="flex items-center gap-2 mb-1">
        <Icon size={15} color="var(--accent)" />
        <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--text-primary)" }}>{title}</span>
      </div>
      {caption && <p style={{ fontSize: "0.68rem", color: "var(--text-dim)", margin: "0 0 14px" }}>{caption}</p>}
      {children}
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

function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
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
