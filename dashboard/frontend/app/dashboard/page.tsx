"use client";

import { useCallback, useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  BookMarked,
  Flame,
  LayoutDashboard,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Zap,
} from "lucide-react";
import { api, type CommandCenterResponse, type DailyBriefResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function SeverityDot({ severity }: { severity: string }) {
  const s = severity.toLowerCase();
  const color =
    s === "high" ? "var(--danger)" : s === "medium" ? "var(--warning)" : "var(--accent)";
  return (
    <span
      aria-hidden
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: 999,
        background: color,
        marginRight: 8,
        flexShrink: 0,
      }}
    />
  );
}

export default function CommandCenterPage() {
  const { user, token, loading: authLoading } = useAuth();
  const [cc, setCc] = useState<CommandCenterResponse | null>(null);
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, b] = await Promise.all([api.commandCenter(token), api.dailyBrief(token)]);
      setCc(c);
      setBrief(b);
      if (token) {
        try {
          await api.sessionPulse(token);
          await api.intelEvent(token, { event: "command_center_open" });
        } catch {
          /* non-fatal */
        }
      }
    } catch {
      setError("Could not load command center. Check backend URL (production uses NEXT_PUBLIC_BACKEND_URL).");
      setCc(null);
      setBrief(null);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!authLoading) load();
  }, [authLoading, load]);

  useEffect(() => {
    return () => {
      if (token) {
        void api.visitMark(token).catch(() => {});
      }
    };
  }, [token]);

  if (authLoading) {
    return (
      <div className="glass" style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>
        Loading…
      </div>
    );
  }

  if (!user) {
    return (
      <div className="glass" style={{ maxWidth: 520, margin: "0 auto", padding: 28 }}>
        <h1 style={{ fontSize: "1.2rem", marginBottom: 12, display: "flex", alignItems: "center", gap: 10 }}>
          <LayoutDashboard size={22} color="var(--accent)" /> Trader Command Center
        </h1>
        <p style={{ color: "var(--text-secondary)", marginBottom: 20, lineHeight: 1.5 }}>
          Sign in to unlock your personal desk, watchlist intelligence, and priority feed.
        </p>
        <Link href="/login" className="btn-accent" style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "10px 20px", textDecoration: "none" }}>
          Sign in <ArrowRight size={16} />
        </Link>
      </div>
    );
  }

  const wm = cc?.what_matters_now ?? [];
  const tierNote = cc?.premium?.note;

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", display: "flex", flexDirection: "column", gap: 16 }}>
      <header style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 12,
                background: "rgba(0,212,255,0.12)",
                border: "1px solid rgba(0,212,255,0.25)",
                display: "grid",
                placeItems: "center",
              }}
            >
              <LayoutDashboard size={22} color="var(--accent)" />
            </div>
            <div>
              <h1 style={{ margin: 0, fontSize: "clamp(1.15rem, 3vw, 1.45rem)", fontWeight: 900 }}>Trader Command Center</h1>
              <p style={{ margin: 0, fontSize: "0.78rem", color: "var(--text-dim)" }}>
                Action-first · snapshot-driven · not advice
              </p>
            </div>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          {user.role === "FREE" && (
            <span style={{ fontSize: "0.68rem", padding: "4px 10px", borderRadius: 999, border: "1px solid rgba(245,158,11,0.35)", color: "var(--warning)" }}>
              Free tier — shorter priority feed
            </span>
          )}
          <button type="button" onClick={load} className="btn-accent" style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 14px", fontSize: "0.78rem" }}>
            <RefreshCw size={14} /> Sync
          </button>
        </div>
      </header>

      {error && (
        <div className="glass" style={{ padding: 12, color: "var(--danger)", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="glass" style={{ padding: 28, textAlign: "center", color: "var(--text-secondary)" }}>
          Loading command center…
        </div>
      ) : (
        <>
          <section className="glass" style={{ padding: 14, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
            <Metric icon={<Zap size={16} />} label="Regime" value={String(cc?.market_regime ?? "—")} />
            <Metric icon={<Sparkles size={16} />} label="Signals today" value={String(cc?.signals_today ?? "—")} />
            <Metric icon={<BookMarked size={16} />} label="Your desk" value={`${cc?.personal_desk_symbols ?? 0} symbols`} />
            <Metric
              icon={<ShieldAlert size={16} />}
              label="Alerts"
              value={`${cc?.alerts_requiring_attention?.length ?? 0} need attention`}
            />
          </section>

          {cc?.trust_banner && (
            <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--text-dim)", lineHeight: 1.5 }}>
              {cc.trust_banner}
            </p>
          )}

          {(cc?.revisit_psychology?.lines?.length ?? 0) > 0 && (
            <section className="glass" style={{ padding: 14, border: "1px solid rgba(168,85,247,0.2)" }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, letterSpacing: 0.06, color: "var(--text-dim)", marginBottom: 10 }}>
                SINCE YOUR LAST CHECK-IN
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>
                {(cc?.revisit_psychology?.lines ?? []).map((ln, i) => (
                  <li key={`rv-${i}`}>{ln}</li>
                ))}
              </ul>
              {cc?.revisit_psychology?.trust_note && (
                <p style={{ margin: "10px 0 0", fontSize: "0.68rem", color: "var(--text-dim)" }}>{cc.revisit_psychology.trust_note}</p>
              )}
            </section>
          )}

          {(cc?.urgency_layer?.rows?.length ?? 0) > 0 && (
            <section className="glass" style={{ padding: 14, border: "1px solid rgba(245,158,11,0.22)" }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--warning)", marginBottom: 10 }}>MARKET / DESK URGENCY</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: "0.72rem", color: "var(--text-secondary)", marginBottom: 8 }}>
                <span>Near-ready: <strong style={{ color: "var(--text-primary)" }}>{cc?.urgency_layer?.near_ready_count ?? 0}</strong></span>
                <span>Weakening: <strong style={{ color: "var(--text-primary)" }}>{cc?.urgency_layer?.weakening_count ?? 0}</strong></span>
              </div>
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                {(cc?.urgency_layer?.rows ?? []).map((u, i) => (
                  <li key={`ug-${u.symbol}-${i}`} style={{ fontSize: "0.78rem", lineHeight: 1.45 }}>
                    <span style={{ color: "var(--accent)", fontWeight: 700 }}>{u.symbol}</span>{" "}
                    <span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>{u.label?.replace(/_/g, " ")}</span>
                    <div style={{ color: "var(--text-secondary)", marginTop: 2 }}>{u.detail}</div>
                  </li>
                ))}
              </ul>
              {cc?.urgency_layer?.trust_note && (
                <p style={{ margin: "10px 0 0", fontSize: "0.68rem", color: "var(--text-dim)" }}>{cc.urgency_layer.trust_note}</p>
              )}
            </section>
          )}

          {(cc?.session_intelligence_today?.strongest_readiness_gains?.length ?? 0) +
            (cc?.session_intelligence_today?.largest_deterioration_stress?.length ?? 0) +
            (cc?.session_intelligence_today?.highest_momentum_names?.length ?? 0) >
            0 && (
            <section className="glass" style={{ padding: 14 }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 10 }}>YOUR DESK — SESSION LENS</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, fontSize: "0.76rem" }}>
                <div>
                  <div style={{ color: "var(--success)", fontSize: "0.58rem", marginBottom: 4 }}>Readiness gains</div>
                  {(cc?.session_intelligence_today?.strongest_readiness_gains ?? []).map((x) => (
                    <div key={`sg-${x.symbol}`} style={{ color: "var(--text-secondary)" }}>
                      {x.symbol} · {x.readiness_pct != null ? `${Math.round(x.readiness_pct)}%` : "—"}
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{ color: "var(--danger)", fontSize: "0.58rem", marginBottom: 4 }}>Stress focus</div>
                  {(cc?.session_intelligence_today?.largest_deterioration_stress ?? []).map((x) => (
                    <div key={`st-${x.symbol}`} style={{ color: "var(--text-secondary)" }}>
                      {x.symbol} · {x.readiness_pct != null ? `${Math.round(x.readiness_pct)}%` : "—"}
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{ color: "var(--accent)", fontSize: "0.58rem", marginBottom: 4 }}>Momentum leaders</div>
                  {(cc?.session_intelligence_today?.highest_momentum_names ?? []).map((x) => (
                    <div key={`mo-${x.symbol}`} style={{ color: "var(--text-secondary)" }}>
                      {x.symbol}
                    </div>
                  ))}
                </div>
              </div>
              {cc?.session_intelligence_today?.trust_note && (
                <p style={{ margin: "10px 0 0", fontSize: "0.68rem", color: "var(--text-dim)" }}>{cc.session_intelligence_today.trust_note}</p>
              )}
            </section>
          )}

          {tierNote && (
            <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--warning)" }}>
              {tierNote}
            </p>
          )}

          <section className="glass" style={{ padding: 16, border: "1px solid rgba(0,212,255,0.2)" }}>
            <div style={{ fontSize: "0.62rem", fontWeight: 800, letterSpacing: 0.08, color: "var(--text-dim)", marginBottom: 12 }}>
              WHAT MATTERS NOW
            </div>
            {wm.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: 0 }}>No priority headlines yet — add symbols to your watchlist or check Research.</p>
            ) : (
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
                {wm.map((line, i) => (
                  <li
                    key={`${line.headline}-${i}`}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 4,
                      fontSize: "0.82rem",
                      lineHeight: 1.45,
                      color: "var(--text-primary)",
                    }}
                  >
                    <SeverityDot severity={line.severity} />
                    <span>
                      {line.symbol && <strong style={{ color: "var(--accent)" }}>{line.symbol} · </strong>}
                      {line.headline}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 12 }}>
            <CardList
              title="Best opportunities (discovery snapshot)"
              icon={<TrendingUp size={16} color="var(--success)" />}
              rows={(cc?.best_opportunities_now ?? []).map((x) => ({
                sym: x.symbol,
                sub: x.note,
                right: x.confidence_score != null ? `${Math.round(x.confidence_score)}` : undefined,
              }))}
              empty="No discovery snapshot — engine may be warming."
            />
            <CardList
              title="Biggest deteriorations (your desk)"
              icon={<TrendingDown size={16} color="var(--danger)" />}
              rows={(cc?.biggest_deteriorations ?? []).slice(0, 8).map((x) => ({
                sym: x.symbol,
                sub: x.validated_overall || `stress ~${x.deterioration_probability_pct ?? "—"}%`,
                right: x.action,
              }))}
              empty="No elevated deterioration flags on saved symbols."
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 12 }}>
            <CardList
              title="Biggest improvements"
              icon={<Flame size={16} color="var(--accent)" />}
              rows={(cc?.biggest_improvements ?? []).map((x) => ({
                sym: x.symbol,
                sub: `Δ readiness ${x.readiness_delta_pct != null ? `${x.readiness_delta_pct > 0 ? "+" : ""}${x.readiness_delta_pct}` : "—"}`,
                right: x.action,
              }))}
              empty="No meaningful readiness lift vs last snapshot."
            />
            <CardList
              title="High-conviction (final review bucket)"
              icon={<Sparkles size={16} color="var(--warning)" />}
              rows={(cc?.active_high_conviction ?? []).map((x) => ({
                sym: x.symbol,
                sub: x.note,
                right: x.confidence_score != null ? `${Math.round(x.confidence_score)}` : undefined,
              }))}
              empty="No final-review names in snapshot."
            />
          </div>

          {(cc?.alerts_requiring_attention?.length ?? 0) > 0 && (
            <section className="glass" style={{ padding: 14, border: "1px solid rgba(239,68,68,0.25)" }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--danger)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
                <AlertTriangle size={14} /> Alerts requiring attention
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                {(cc?.alerts_requiring_attention ?? []).map((a) => (
                  <li key={a.symbol + a.action}>
                    <strong style={{ color: "var(--accent)" }}>{a.symbol}</strong> — {a.action}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {cc?.portfolio_rotation?.desks && Object.keys(cc.portfolio_rotation.desks).length > 0 && (
            <section className="glass" style={{ padding: 14 }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 8 }}>PORTFOLIO ROTATION (virtual desks)</div>
              <p style={{ fontSize: "0.72rem", color: "var(--text-secondary)", margin: "0 0 10px" }}>{cc.portfolio_rotation.note}</p>
              <DeskRow label="Intraday" syms={cc.portfolio_rotation.desks.intraday_momentum} />
              <DeskRow label="MTF swing" syms={cc.portfolio_rotation.desks.mtf_swing} />
              <DeskRow label="Short-term growth" syms={cc.portfolio_rotation.desks.short_term_growth} />
              <DeskRow label="Long-term" syms={cc.portfolio_rotation.desks.long_term_compounders} />
            </section>
          )}

          {(cc?.watchlist_feed_preview?.length ?? 0) > 0 && (
            <section className="glass" style={{ padding: 14 }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 8 }}>Desk evolution feed</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 200, overflowY: "auto" }}>
                {(cc?.watchlist_feed_preview ?? []).map((f, i) => (
                  <div key={`${f.ts}-${i}`} style={{ fontSize: "0.76rem", borderBottom: "1px solid rgba(255,255,255,0.06)", paddingBottom: 6 }}>
                    <span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>{f.ts?.slice(11, 19)}</span>{" "}
                    <strong style={{ color: "var(--accent)" }}>{f.symbol}</strong> — {f.headline}
                  </div>
                ))}
              </div>
            </section>
          )}

          {brief?.sections && brief.sections.length > 0 && (
            <section className="glass" style={{ padding: 16 }}>
              <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 12 }}>DAILY MARKET BRIEF</div>
              {brief.trust_note && (
                <p style={{ fontSize: "0.72rem", color: "var(--text-dim)", margin: "0 0 12px" }}>{brief.trust_note}</p>
              )}
              {(brief.narrative_sections?.length ?? 0) > 0 && (
                <div style={{ marginBottom: 14, paddingBottom: 12, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                  <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--accent)", marginBottom: 8 }}>MARKET STORY (YOUR DESK + REGIME)</div>
                  {(brief.narrative_sections ?? []).map((s, ni) => (
                    <div key={`nar-${ni}-${s.title ?? "sec"}`} style={{ marginBottom: 10 }}>
                      <div style={{ fontWeight: 800, fontSize: "0.78rem", marginBottom: 4 }}>{s.title}</div>
                      <p style={{ margin: 0, fontSize: "0.76rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>{s.body}</p>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {brief.sections.map((s, si) => (
                  <div key={`brief-${si}-${s.title ?? "sec"}`}>
                    <div style={{ fontWeight: 800, fontSize: "0.82rem", marginBottom: 4 }}>{s.title}</div>
                    <p style={{ margin: 0, fontSize: "0.78rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>{s.body}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            <Link href="/watchlist" style={{ ...pillLinkStyle, borderColor: "rgba(0,212,255,0.35)" }}>
              Personal trading desk <ArrowRight size={14} />
            </Link>
            <Link href="/research" style={{ ...pillLinkStyle, borderColor: "rgba(255,255,255,0.12)" }}>
              AI Research Center <ArrowRight size={14} />
            </Link>
          </div>
        </>
      )}
    </div>
  );
}

const pillLinkStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  padding: "10px 16px",
  borderRadius: 10,
  border: "1px solid",
  fontSize: "0.82rem",
  fontWeight: 700,
  color: "var(--text-primary)",
  textDecoration: "none",
};

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={{ padding: 10, borderRadius: 10, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
      <div style={{ fontSize: "0.58rem", color: "var(--text-dim)", marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
        {icon}
        {label}
      </div>
      <div style={{ fontWeight: 800, fontSize: "0.95rem" }}>{value}</div>
    </div>
  );
}

function CardList({
  title,
  icon,
  rows,
  empty,
}: {
  title: string;
  icon: React.ReactNode;
  rows: { sym: string; sub?: string; right?: string }[];
  empty: string;
}) {
  return (
    <section className="glass" style={{ padding: 14, minHeight: 140 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)" }}>
        {icon}
        {title}
      </div>
      {rows.length === 0 ? (
        <p style={{ margin: 0, fontSize: "0.78rem", color: "var(--text-secondary)" }}>{empty}</p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          {rows.map((r) => (
            <li key={r.sym} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: "0.78rem", alignItems: "baseline" }}>
              <div>
                <strong style={{ color: "var(--accent)" }}>{r.sym}</strong>
                {r.sub && <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", marginTop: 2 }}>{r.sub}</div>}
              </div>
              {r.right && <span style={{ fontSize: "0.68rem", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>{r.right}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DeskRow({ label, syms }: { label: string; syms?: string[] }) {
  const list = syms?.filter(Boolean) ?? [];
  if (list.length === 0) return null;
  return (
    <div style={{ marginBottom: 8, fontSize: "0.76rem" }}>
      <span style={{ color: "var(--text-dim)", marginRight: 8 }}>{label}</span>
      <span style={{ color: "var(--text-primary)" }}>{list.slice(0, 12).join(", ")}</span>
    </div>
  );
}
