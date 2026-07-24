"use client";

/**
 * /command — Command Center (redesign).
 *
 * Value-first homepage: leads with the engine's Top Opportunities + a Verified
 * Track Record so a new paid user sees "why this is different" in ~5 seconds.
 * Adaptive: new users get guided onboarding; returning users get their desk.
 *
 * UX/layout/copy only — reuses EXISTING endpoints:
 *   • /api/command-center      → opportunities, watchlist feed, priority lines
 *   • /api/market/daily-brief  → regime + discovery narrative
 *   • /api/analytics/track-record → verified hit-rate / avg return (real)
 *   • live engine snapshot      → regime, daily P&L, signals
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  TrendingUp, TrendingDown, Minus, Eye, Sparkles, ArrowRight, ChevronDown,
  Newspaper, Activity, ShieldCheck, Search, Info,
} from "lucide-react";
import { api, type CommandCenterResponse, type DailyBriefResponse, type TrackRecordSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useEngineSocket } from "@/lib/useWebSocket";
import { marketPhase } from "@/lib/nba";
import { humanize, regimeContext } from "@/lib/humanize";
import AddToWatchlistButton from "@/components/AddToWatchlistButton";
import Sparkline from "@/components/Sparkline";
import { ExposureRegimePanel } from "./ExposureRegimePanel";

// Freshness — relative "updated X ago" (Refinement 3).
function relTime(iso?: string | null, now = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)}h ago`;
}

function regimeMeta(r?: string | null) {
  const v = String(r || "").toUpperCase();
  if (v.includes("BULL")) return { label: "BULLISH", color: "var(--success)", Icon: TrendingUp };
  if (v.includes("BEAR")) return { label: "BEARISH", color: "var(--danger)", Icon: TrendingDown };
  if (v.includes("CHOP") || v.includes("RISK")) return { label: v, color: "var(--warning)", Icon: Minus };
  return { label: v || "NEUTRAL", color: "var(--text-secondary)", Icon: Minus };
}

// SMC score → star tier + plain-language label (Refinement 3: never a bare 96/100).
function scoreTier(score: number): { stars: number; label: string; color: string } {
  if (score >= 90) return { stars: 5, label: "Excellent", color: "#34d399" };
  if (score >= 80) return { stars: 4, label: "Strong", color: "#34d399" };
  if (score >= 70) return { stars: 3, label: "Good", color: "#22d3ee" };
  if (score >= 60) return { stars: 2, label: "Fair", color: "#fbbf24" };
  return { stars: 1, label: "Building", color: "#fbbf24" };
}

// What the SMC score blends — shown in the "What builds this score?" explainer.
const SCORE_FACTORS = [
  "Market structure (BOS / CHoCH)",
  "Order blocks (demand / supply)",
  "Fair value gaps (FVG)",
  "Liquidity sweeps",
  "Volume expansion",
  "Trend alignment",
];

function sym(s?: string | null) {
  return String(s || "").replace("NSE:", "").trim().toUpperCase();
}

const FAMILIAR = ["RELIANCE", "TCS", "HDFCBANK", "INFY"];

export default function CommandCenterPage() {
  const { user, token } = useAuth();
  const { snapshot } = useEngineSocket();
  const [cc, setCc] = useState<CommandCenterResponse | null>(null);
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [track, setTrack] = useState<TrackRecordSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showBrief, setShowBrief] = useState(false);
  const [spark, setSpark] = useState<Record<string, number[]>>({});
  const [nowTick, setNowTick] = useState(() => Date.now());

  // Tick the "updated X ago" label without re-fetching.
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let live = true;
    Promise.allSettled([
      api.commandCenter(token ?? undefined),
      api.dailyBrief(token ?? undefined),
      api.trackRecord("all", 100),
    ])
      .then(([c, b, t]) => {
        if (!live) return;
        if (c.status === "fulfilled") setCc(c.value);
        if (b.status === "fulfilled") setBrief(b.value);
        if (t.status === "fulfilled") setTrack(t.value.summary);
      })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [token]);

  const phase = marketPhase();
  const regime = cc?.market_regime ?? snapshot?.market_regime ?? brief?.regime;
  const rm = regimeMeta(regime as string);
  const pnlR = snapshot?.daily_pnl_r;
  const signalsToday = (cc?.signals_today as number) ?? snapshot?.signals_today ?? 0;
  const deskCount = cc?.personal_desk_symbols ?? 0;
  const feed = (cc?.watchlist_feed_preview ?? []).slice(0, 6);
  const moodLine = regimeContext(regime as string);
  const marketPhaseLabel = phase.marketOpen ? "MARKET LIVE" : phase.preOpen ? "PRE-OPEN" : "MARKET CLOSED";

  // Consolidated, de-duplicated opportunities (Refinement 2: no repeats).
  const opportunities = useMemo(() => {
    const seen = new Set<string>();
    const list: { symbol: string; note?: string; score: number }[] = [];
    for (const o of [...(cc?.best_opportunities_now ?? []), ...(cc?.active_high_conviction ?? [])]) {
      const s = sym(o.symbol);
      if (!s || seen.has(s)) continue;
      seen.add(s);
      list.push({ symbol: s, note: o.note, score: Number(o.confidence_score || 0) });
    }
    return list.sort((a, b) => b.score - a.score);
  }, [cc]);
  const top3 = opportunities.slice(0, 3);
  const more = opportunities.slice(3, 8);
  const top3Key = top3.map((o) => o.symbol).join(",");

  // Lightweight sparklines for the top-3 only — backgrounded after the main
  // render so it never delays the hero. Reuses the existing chart-data endpoint.
  useEffect(() => {
    const syms = top3Key ? top3Key.split(",") : [];
    if (syms.length === 0) return;
    let live = true;
    Promise.allSettled(syms.map((s) => api.researchChartData(s, "SWING"))).then((results) => {
      if (!live) return;
      const next: Record<string, number[]> = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled" && Array.isArray(r.value.candles)) {
          const closes = r.value.candles.map((c) => c.close).filter((n): n is number => typeof n === "number");
          if (closes.length >= 2) next[syms[i]] = closes.slice(-30);
        }
      });
      setSpark(next);
    });
    return () => { live = false; };
  }, [top3Key]);

  // Freshness (Refinement 3)
  const engineVer = snapshot?._global_state_version ?? cc?._global_state_version;
  const updatedLabel = relTime(snapshot?.snapshot_time, nowTick);

  const hasPersonal = deskCount > 0 || feed.length > 0;

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 80, color: "var(--text-secondary)", fontSize: "0.9rem" }}>
        Loading today&apos;s dashboard…
      </div>
    );
  }

  return (
    <div className="w-full max-w-screen-xl mx-auto flex flex-col gap-4">
      {/* Header — value-first, not a greeting (Refinement 4) */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-lg md:text-2xl font-extrabold m-0" style={{ color: "var(--text-primary)" }}>
            Today&apos;s Trading Dashboard
          </h1>
          <p className="m-0 flex items-center gap-2" style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>
            <span style={{ color: rm.color, fontWeight: 700 }}>● {rm.label}</span>
            <span>·</span>
            <span>{moodLine.split("—")[0].trim()}</span>
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-4">
            <MiniStat label="Signals" value={String(signalsToday)} />
            {user && typeof pnlR === "number" && (
              <MiniStat label="Your day" value={`${pnlR >= 0 ? "+" : ""}${pnlR.toFixed(2)}R`} color={pnlR >= 0 ? "var(--success)" : "var(--danger)"} />
            )}
            <span className="badge" style={{ fontSize: "0.62rem", color: "var(--text-secondary)" }}>
              <span className="pulse-dot" style={{ width: 6, height: 6, borderRadius: "50%", background: phase.marketOpen ? "var(--success)" : "var(--text-dim)", display: "inline-block" }} />
              {marketPhaseLabel}
            </span>
          </div>
          {(updatedLabel || engineVer) && (
            <div style={{ fontSize: "0.6rem", color: "var(--text-dim)" }}>
              {updatedLabel && <>Updated {updatedLabel}</>}
              {updatedLabel && engineVer ? " · " : ""}
              {engineVer ? `Engine v${engineVer}` : ""}
            </div>
          )}
        </div>
      </div>

      {/* Market regime + exposure — first thing users see (PR2) */}
      <ExposureRegimePanel ideaCount={opportunities.length} />

      {/* ★ HERO — Today's Top Opportunities (top 3, expandable) */}
      <div className="glass rounded-xl" style={{ padding: "16px 18px", border: "1px solid rgba(16,185,129,0.3)", boxShadow: "0 18px 44px rgba(16,185,129,0.10)" }}>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Sparkles size={17} color="#34d399" />
            <span style={{ fontSize: "0.9rem", fontWeight: 800, color: "var(--text-primary)" }}>Today&apos;s Top Opportunities</span>
          </div>
          <Link href="/research" className="inline-flex items-center gap-1" style={{ fontSize: "0.7rem", color: "var(--accent)", textDecoration: "none" }}>
            All on the radar <ArrowRight size={12} />
          </Link>
        </div>
        {top3.length === 0 ? (
          <Empty>No A-grade setups in the current scan. The engine is still monitoring structure — check Research for names approaching confirmation.</Empty>
        ) : (
          <div className="flex flex-col gap-2">
            {top3.map((o, i) => {
              const tier = scoreTier(o.score);
              const open = expanded === o.symbol;
              return (
                <div key={o.symbol} style={{ borderRadius: 11, border: `1px solid ${open ? tier.color : "rgba(255,255,255,0.06)"}`, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
                  <button
                    type="button"
                    onClick={() => setExpanded(open ? null : o.symbol)}
                    className="w-full flex items-center gap-3"
                    style={{ padding: "12px 14px", background: "none", border: "none", cursor: "pointer", textAlign: "left" }}
                  >
                    <span style={{ fontSize: "0.9rem", fontWeight: 800, color: "var(--text-dim)", width: 18 }}>{i + 1}</span>
                    <span style={{ fontSize: "0.98rem", fontWeight: 800, color: "var(--text-primary)", minWidth: 96 }}>{o.symbol}</span>
                    <span className="flex items-center gap-2" style={{ minWidth: 0 }}>
                      <span style={{ fontSize: "1.05rem", fontWeight: 800, color: tier.color, fontVariantNumeric: "tabular-nums" }}>{o.score.toFixed(0)}</span>
                      <span style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>/100</span>
                    </span>
                    <span className="hidden sm:flex items-center gap-1.5">
                      <Stars n={tier.stars} color={tier.color} />
                      <span style={{ fontSize: "0.72rem", color: tier.color, fontWeight: 700 }}>{tier.label}</span>
                    </span>
                    {spark[o.symbol] && spark[o.symbol].length >= 2 && (
                      <span style={{ marginLeft: "auto" }}>
                        <Sparkline data={spark[o.symbol]} positive={spark[o.symbol][spark[o.symbol].length - 1] >= spark[o.symbol][0]} />
                      </span>
                    )}
                    <ChevronDown size={16} style={{ marginLeft: spark[o.symbol] ? 8 : "auto", color: "var(--text-dim)", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s", flexShrink: 0 }} />
                  </button>

                  {open && (
                    <div style={{ padding: "0 14px 14px", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
                      <div className="flex sm:hidden items-center gap-1.5 mt-3">
                        <Stars n={tier.stars} color={tier.color} />
                        <span style={{ fontSize: "0.72rem", color: tier.color, fontWeight: 700 }}>{tier.label}</span>
                      </div>
                      <div style={{ marginTop: 12 }}>
                        <div className="flex items-center gap-1.5" style={{ fontSize: "0.66rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-dim)", marginBottom: 6 }}>
                          <Info size={12} /> What builds this score?
                        </div>
                        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                          {SCORE_FACTORS.map((f) => (
                            <div key={f} className="flex items-center gap-1.5" style={{ fontSize: "0.74rem", color: "var(--text-secondary)" }}>
                              <span style={{ color: tier.color }}>✓</span> {f}
                            </div>
                          ))}
                        </div>
                        <p style={{ fontSize: "0.7rem", color: "var(--text-dim)", margin: "8px 0 0" }}>
                          {humanize(o.note || "")} The SMC score (0–100) blends the confluence factors above — higher = more aligned.
                        </p>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 mt-3">
                        <Link
                          href={`/research/chart?symbol=${encodeURIComponent(o.symbol)}&horizon=SWING`}
                          className="inline-flex items-center gap-1.5 rounded-lg font-semibold"
                          style={{ padding: "8px 14px", fontSize: "0.78rem", background: "#34d399", color: "#04130d", textDecoration: "none" }}
                        >
                          View Full Analysis <ArrowRight size={14} />
                        </Link>
                        <AddToWatchlistButton symbol={o.symbol} compact />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "10px 0 0" }}>
          Ranked by SMC score. Analysis of live market structure — not buy/sell advice.
        </p>
      </div>

      {/* ★ Verified Track Record (Refinement 2: trust as a strong element, up top) */}
      <div className="glass rounded-xl" style={{ padding: "16px 18px", border: "1px solid rgba(34,211,238,0.25)" }}>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <ShieldCheck size={17} color="var(--accent)" />
            <span style={{ fontSize: "0.9rem", fontWeight: 800, color: "var(--text-primary)" }}>Verified Track Record</span>
          </div>
          <Link href="/research/track-record" className="inline-flex items-center gap-1" style={{ fontSize: "0.7rem", color: "var(--accent)", textDecoration: "none" }}>
            View Performance <ArrowRight size={12} />
          </Link>
        </div>
        {track && track.resolved > 0 ? (
          // Proof of activity + transparency first; statistical quality (hit
          // rate) lives on the dedicated Track Record page (Refinement 1).
          <div className="grid grid-cols-3 gap-3">
            <TrustStat label="Resolved Setups" value={String(track.resolved)} sub={`of ${track.total_picks} tracked`} big />
            <TrustStat label="Average Return" value={`${track.avg_pnl_pct > 0 ? "+" : ""}${track.avg_pnl_pct}%`} color={track.avg_pnl_pct >= 0 ? "var(--success)" : "var(--danger)"} big />
            <TrustStat label="Last Updated" value="Today" sub="live, verified" />
          </div>
        ) : (
          <Empty>Building a verified track record — hit-rate and average return appear as setups resolve. Every number ties to a logged, timestamped call.</Empty>
        )}
        <p style={{ fontSize: "0.64rem", color: "var(--text-dim)", margin: "10px 0 0" }}>
          Computed only over <b>resolved</b> algorithmic setups (hypothetical, at the levels shown). Past performance doesn&apos;t guarantee future results.
        </p>
      </div>

      {/* Adaptive band — onboarding (new) OR desk (returning) */}
      {!hasPersonal ? (
        <div className="glass rounded-xl" style={{ padding: "18px", border: "1px solid rgba(245,158,11,0.25)" }}>
          <div className="flex items-center gap-2 mb-1">
            <Search size={17} color="var(--warning)" />
            <span style={{ fontSize: "0.9rem", fontWeight: 800, color: "var(--text-primary)" }}>Get started in 60 seconds</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap my-3" style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>
            {["Search", "Analyze", "Add to Watchlist", "Get alerts"].map((step, i) => (
              <span key={step} className="flex items-center gap-2">
                <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <span style={{ width: 18, height: 18, borderRadius: "50%", background: "var(--accent-dim)", color: "var(--accent)", fontSize: "0.62rem", fontWeight: 800, display: "grid", placeItems: "center" }}>{i + 1}</span>
                  {step}
                </span>
                {i < 3 && <ArrowRight size={12} style={{ color: "var(--text-dim)" }} />}
              </span>
            ))}
          </div>
          <div style={{ fontSize: "0.74rem", color: "var(--text-dim)", marginBottom: 8 }}>Track a stock you already follow:</div>
          <div className="flex flex-wrap gap-2">
            {FAMILIAR.map((s) => (
              <Link key={s} href={`/research/chart?symbol=${s}&horizon=SWING`} className="inline-flex items-center gap-1.5 rounded-lg font-semibold" style={{ padding: "7px 13px", fontSize: "0.78rem", background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.25)", color: "var(--text-primary)", textDecoration: "none" }}>
                {s}
              </Link>
            ))}
            <Link href="/research" className="inline-flex items-center gap-1.5 rounded-lg" style={{ padding: "7px 13px", fontSize: "0.78rem", background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)", color: "var(--text-secondary)", textDecoration: "none" }}>
              <Search size={13} /> Search any NSE stock
            </Link>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Panel title="Your watchlist" icon={Eye} href="/watchlist" cta="Open feed">
            {feed.length === 0 ? (
              <Empty>No new events yet today.</Empty>
            ) : (
              <div className="flex flex-col gap-1.5">
                {feed.map((e, i) => (
                  <Link key={i} href={e.symbol ? `/stock/${sym(e.symbol)}` : "/watchlist"} className="flex items-center gap-2.5 rounded-lg group" style={{ padding: "8px 10px", textDecoration: "none", background: "rgba(255,255,255,0.02)" }}>
                    <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "var(--accent)", minWidth: 54 }}>{sym(e.symbol) || "—"}</span>
                    <span style={{ flex: 1, fontSize: "0.8rem", color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {humanize(e.headline || e.type || e.setup_status || "Update")}
                    </span>
                    <ArrowRight size={13} className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ color: "var(--accent)" }} />
                  </Link>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="More opportunities" icon={Sparkles} href="/research" cta="Research">
            {more.length === 0 ? (
              <Empty>The top setups are all above — nothing more in the current scan.</Empty>
            ) : (
              <div className="flex flex-col gap-1.5">
                {more.map((o) => {
                  const tier = scoreTier(o.score);
                  return (
                    <Link key={o.symbol} href={`/research/chart?symbol=${encodeURIComponent(o.symbol)}&horizon=SWING`} className="flex items-center gap-2.5 rounded-lg group" style={{ padding: "8px 10px", textDecoration: "none", background: "rgba(255,255,255,0.02)" }}>
                      <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--text-primary)", minWidth: 90 }}>{o.symbol}</span>
                      <span style={{ fontSize: "0.82rem", fontWeight: 800, color: tier.color }}>{o.score.toFixed(0)}</span>
                      <span style={{ fontSize: "0.68rem", color: tier.color }}>{tier.label}</span>
                      <ArrowRight size={13} className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ color: "var(--accent)", marginLeft: "auto" }} />
                    </Link>
                  );
                })}
              </div>
            )}
          </Panel>
        </div>
      )}

      {/* Today's brief — supporting context, collapsible */}
      {(brief?.sections ?? []).length > 0 && (
        <div className="glass rounded-xl" style={{ padding: "14px 16px" }}>
          <button type="button" onClick={() => setShowBrief((s) => !s)} className="w-full flex items-center justify-between" style={{ background: "none", border: "none", cursor: "pointer" }}>
            <div className="flex items-center gap-2">
              <Newspaper size={15} color="var(--accent)" />
              <span style={{ fontSize: "0.82rem", fontWeight: 700, color: "var(--text-primary)" }}>Today&apos;s brief</span>
            </div>
            <ChevronDown size={16} style={{ color: "var(--text-dim)", transform: showBrief ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />
          </button>
          {showBrief && (
            <div className="flex flex-col gap-2.5 mt-3">
              {(brief?.sections ?? []).slice(0, 3).map((s, i) => (
                <div key={i}>
                  <div style={{ fontSize: "0.66rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--accent)", marginBottom: 2 }}>{s.title}</div>
                  <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>{humanize(s.body)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Option A disclaimer */}
      <p style={{ fontSize: "0.66rem", color: "var(--text-dim)", textAlign: "center", margin: "2px 0 0", display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
        <Activity size={11} />
        {humanize(cc?.trust_banner || "") || "Signals, scores and labels are analysis from live market data — not trade instructions."}
      </p>
    </div>
  );
}

function Stars({ n, color }: { n: number; color: string }) {
  return (
    <span style={{ letterSpacing: "1px", fontSize: "0.8rem", lineHeight: 1 }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} style={{ color: i <= n ? color : "var(--text-dim)", opacity: i <= n ? 1 : 0.4 }}>★</span>
      ))}
    </span>
  );
}

function MiniStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ textAlign: "right" }}>
      <div style={{ fontSize: "0.55rem", color: "var(--text-dim)", letterSpacing: "0.05em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "0.9rem", fontWeight: 700, color: color ?? "var(--text-primary)" }}>{value}</div>
    </div>
  );
}

function TrustStat({ label, value, sub, color, big }: { label: string; value: string; sub?: string; color?: string; big?: boolean }) {
  return (
    <div className="rounded-lg" style={{ padding: "12px 14px", background: "rgba(255,255,255,0.02)", border: "1px solid var(--border)" }}>
      <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", letterSpacing: "0.05em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: big ? "1.7rem" : "1.25rem", fontWeight: 800, marginTop: 3, color: color ?? "var(--text-primary)", lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function Panel({ title, icon: Icon, href, cta, children }: { title: string; icon: typeof Eye; href: string; cta: string; children: React.ReactNode }) {
  return (
    <div className="glass rounded-xl" style={{ padding: "14px 16px" }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={15} color="var(--accent)" />
          <span style={{ fontSize: "0.82rem", fontWeight: 700, color: "var(--text-primary)" }}>{title}</span>
        </div>
        <Link href={href} className="inline-flex items-center gap-1" style={{ fontSize: "0.68rem", color: "var(--accent)", textDecoration: "none" }}>
          {cta} <ArrowRight size={11} />
        </Link>
      </div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: "0.78rem", color: "var(--text-dim)", padding: "10px 4px", lineHeight: 1.5 }}>{children}</div>;
}
