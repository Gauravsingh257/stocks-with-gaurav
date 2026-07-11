"use client";

/**
 * /command — Morning Command Center (Sprint 1, V1).
 *
 * The default authenticated home. One screen that answers "what deserves my
 * attention today?" within 15 seconds, without opening five pages.
 *
 * Reuse-first: every widget is fed by an EXISTING endpoint —
 *   • /api/command-center  → what_matters_now, opportunities, watchlist feed, alerts
 *   • /api/market/daily-brief → regime + discovery narrative
 *   • live engine snapshot (WebSocket) → regime, daily P&L, signals
 * The only new logic is the rule-based NBA ranking (lib/nba) rendered up top.
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Sunrise, TrendingUp, TrendingDown, Minus, Eye, Sparkles, ArrowRight,
  AlertTriangle, Newspaper, Activity,
} from "lucide-react";
import { api, type CommandCenterResponse, type DailyBriefResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useEngineSocket } from "@/lib/useWebSocket";
import { computeNBA, marketPhase } from "@/lib/nba";
import { NBACard } from "@/components/NextBestAction";
import { humanize, regimeContext } from "@/lib/humanize";

function greeting(): string {
  const utc = Date.now() + new Date().getTimezoneOffset() * 60000;
  const h = new Date(utc + 5.5 * 3600000).getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function regimeMeta(r?: string | null) {
  const v = String(r || "").toUpperCase();
  if (v.includes("BULL")) return { label: "BULLISH", color: "var(--success)", Icon: TrendingUp };
  if (v.includes("BEAR")) return { label: "BEARISH", color: "var(--danger)", Icon: TrendingDown };
  if (v.includes("CHOP") || v.includes("RISK")) return { label: v, color: "var(--warning)", Icon: Minus };
  return { label: v || "NEUTRAL", color: "var(--text-secondary)", Icon: Minus };
}

const SEV_COLOR: Record<string, string> = {
  high: "var(--danger)", medium: "var(--warning)", low: "var(--accent)", info: "var(--text-secondary)",
};

function sym(s?: string | null) {
  return String(s || "").replace("NSE:", "").trim().toUpperCase();
}

export default function CommandCenterPage() {
  const { user, token } = useAuth();
  const { snapshot } = useEngineSocket();
  const [cc, setCc] = useState<CommandCenterResponse | null>(null);
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    Promise.allSettled([api.commandCenter(token ?? undefined), api.dailyBrief(token ?? undefined)])
      .then(([c, b]) => {
        if (!live) return;
        if (c.status === "fulfilled") setCc(c.value);
        if (b.status === "fulfilled") setBrief(b.value);
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

  const actions = useMemo(
    () => computeNBA(cc, { ...phase, regime: regime ? String(regime) : null, watchlistCount: deskCount }),
    [cc, phase, regime, deskCount],
  );

  const matters = (cc?.what_matters_now ?? []).slice(0, 6);
  const feed = (cc?.watchlist_feed_preview ?? []).slice(0, 6);
  const opportunities = useMemo(() => {
    const seen = new Set<string>();
    const list: { symbol: string; note?: string; score?: number }[] = [];
    for (const o of [...(cc?.best_opportunities_now ?? []), ...(cc?.active_high_conviction ?? [])]) {
      const s = sym(o.symbol);
      if (!s || seen.has(s)) continue;
      seen.add(s);
      list.push({ symbol: s, note: o.note, score: o.confidence_score });
    }
    return list.slice(0, 8);
  }, [cc]);

  // "So what?" — a plain-language read of today's regime (Option A: general
  // market context, not a per-stock instruction).
  const moodLine = regimeContext(regime as string);
  const marketPhaseLabel = phase.marketOpen ? "MARKET LIVE" : phase.preOpen ? "PRE-OPEN" : "MARKET CLOSED";

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 80, color: "var(--text-secondary)", fontSize: "0.9rem" }}>
        Loading your command center…
      </div>
    );
  }

  return (
    <div className="w-full max-w-screen-xl mx-auto flex flex-col gap-4">
      {/* Greeting */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2.5">
          <Sunrise size={22} color="var(--accent)" />
          <div>
            <h1 className="text-lg md:text-xl font-extrabold m-0" style={{ color: "var(--text-primary)" }}>
              {greeting()}{user?.name ? `, ${user.name.split(" ")[0]}` : ""}
            </h1>
            <p className="m-0" style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>
              Here&apos;s what deserves your attention today
            </p>
          </div>
        </div>
        <span className="badge" style={{ fontSize: "0.62rem", color: "var(--text-secondary)" }}>
          <span className="pulse-dot" style={{ width: 6, height: 6, borderRadius: "50%", background: phase.marketOpen ? "var(--success)" : "var(--text-dim)", display: "inline-block" }} />
          {marketPhaseLabel}
        </span>
      </div>

      {/* ① Market Mood banner */}
      <div className="glass rounded-xl" style={{ padding: "16px 18px", borderLeft: `3px solid ${rm.color}` }}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="grid place-items-center rounded-lg shrink-0" style={{ width: 40, height: 40, background: `color-mix(in srgb, ${rm.color} 16%, transparent)`, color: rm.color }}>
              <rm.Icon size={20} />
            </div>
            <div className="min-w-0">
              <div style={{ fontSize: "0.6rem", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--text-dim)" }}>Today&apos;s market mood</div>
              <div style={{ fontWeight: 700, color: rm.color, fontSize: "1.05rem" }}>{rm.label}</div>
            </div>
          </div>
          <div className="flex items-center gap-5">
            <Stat label="Signals" value={String(signalsToday)} />
            {user && typeof pnlR === "number" && (
              <Stat label="Daily P&L" value={`${pnlR >= 0 ? "+" : ""}${pnlR.toFixed(2)}R`} color={pnlR >= 0 ? "var(--success)" : "var(--danger)"} />
            )}
            <Stat label="Watching" value={String(deskCount)} />
          </div>
        </div>
        <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)", margin: "10px 0 0", lineHeight: 1.55 }}>{moodLine}</p>
      </div>

      {/* ② Next Best Action — the hero */}
      {actions[0] && <NBACard action={actions[0]} hero />}
      {actions.length > 1 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {actions.slice(1, 3).map((a) => <NBACard key={a.id} action={a} />)}
        </div>
      )}

      {/* Empty state for brand-new users */}
      {user && deskCount === 0 && feed.length === 0 && (
        <div className="glass rounded-xl" style={{ padding: "20px", textAlign: "center" }}>
          <Eye size={22} color="var(--accent)" style={{ margin: "0 auto 8px" }} />
          <div style={{ fontWeight: 700, color: "var(--text-primary)", marginBottom: 4 }}>Your watchlist is empty</div>
          <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)", maxWidth: 420, margin: "0 auto 12px" }}>
            Add a few stocks and we&apos;ll monitor them for you — events show up here and in your morning brief.
          </p>
          <Link href="/research" className="inline-flex items-center gap-1.5 rounded-lg font-semibold" style={{ padding: "8px 14px", fontSize: "0.8rem", background: "var(--accent-dim)", border: "1px solid var(--accent)", color: "var(--accent)", textDecoration: "none" }}>
            Find stocks to watch <ArrowRight size={14} />
          </Link>
        </div>
      )}

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* ③ What deserves attention */}
        <Panel title="What deserves your attention" icon={AlertTriangle} href="/watchlist" cta="Watchlist">
          {matters.length === 0 ? (
            <Empty>No priority items right now — the desk is quiet.</Empty>
          ) : (
            <div className="flex flex-col gap-1.5">
              {matters.map((ln, i) => {
                const c = SEV_COLOR[String(ln.severity).toLowerCase()] ?? "var(--text-secondary)";
                const href = ln.symbol ? `/stock/${sym(ln.symbol)}` : "/watchlist";
                return (
                  <Link key={i} href={href} className="flex items-start gap-2.5 rounded-lg group" style={{ padding: "8px 10px", textDecoration: "none", background: "rgba(255,255,255,0.02)" }}>
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: c, marginTop: 6, flexShrink: 0 }} />
                    <span style={{ flex: 1, fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.45 }}>{humanize(ln.headline)}</span>
                    <ArrowRight size={13} className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ color: c, marginTop: 3 }} />
                  </Link>
                );
              })}
            </div>
          )}
        </Panel>

        {/* ④ Your watchlist — event feed */}
        <Panel title="Your watchlist" icon={Eye} href="/watchlist" cta="Open feed">
          {feed.length === 0 ? (
            <Empty>{deskCount === 0 ? "No stocks yet — add a few from Research." : "No new events yet today."}</Empty>
          ) : (
            <div className="flex flex-col gap-1.5">
              {feed.map((e, i) => (
                <Link key={i} href={e.symbol ? `/stock/${sym(e.symbol)}` : "/watchlist"} className="flex items-center gap-2.5 rounded-lg group" style={{ padding: "8px 10px", textDecoration: "none", background: "rgba(255,255,255,0.02)" }}>
                  <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "var(--accent)", minWidth: 54 }}>{sym(e.symbol) || "—"}</span>
                  <span style={{ flex: 1, fontSize: "0.8rem", color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {e.headline || e.type || e.setup_status || "Update"}
                  </span>
                  <ArrowRight size={13} className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ color: "var(--accent)" }} />
                </Link>
              ))}
            </div>
          )}
        </Panel>

        {/* ⑤ Today's opportunities */}
        <Panel title="Today's opportunities" icon={Sparkles} href="/research" cta="Research">
          {opportunities.length === 0 ? (
            <Empty>No discovery names in the current snapshot.</Empty>
          ) : (
            <div className="flex flex-wrap gap-2">
              {opportunities.map((o) => (
                <Link key={o.symbol} href={`/stock/${o.symbol}`} className="inline-flex items-center gap-2 rounded-lg group" style={{ padding: "7px 11px", textDecoration: "none", background: "rgba(34,211,238,0.06)", border: "1px solid rgba(34,211,238,0.18)" }}>
                  <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--text-primary)" }}>{o.symbol}</span>
                  {typeof o.score === "number" && o.score > 0 && (
                    <span style={{ fontSize: "0.66rem", color: "var(--accent)" }}>{o.score.toFixed(0)}</span>
                  )}
                </Link>
              ))}
            </div>
          )}
        </Panel>

        {/* ⑥ Today's brief */}
        <Panel title="Today's brief" icon={Newspaper} href="/market-intelligence" cta="Market intel">
          {(brief?.sections ?? []).length === 0 ? (
            <Empty>Brief loads with market context.</Empty>
          ) : (
            <div className="flex flex-col gap-2.5">
              {(brief?.sections ?? []).slice(0, 3).map((s, i) => (
                <div key={i}>
                  <div style={{ fontSize: "0.66rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--accent)", marginBottom: 2 }}>{s.title}</div>
                  <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>{humanize(s.body)}</div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {/* Trust note */}
      <p style={{ fontSize: "0.68rem", color: "var(--text-dim)", textAlign: "center", margin: "4px 0 0", display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
        <Activity size={11} />
        {humanize(cc?.trust_banner || "") || "Signals and labels are analysis from live market data — not trade instructions."}
      </p>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ textAlign: "right" }}>
      <div style={{ fontSize: "0.58rem", color: "var(--text-dim)", letterSpacing: "0.05em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "0.95rem", fontWeight: 700, color: color ?? "var(--text-primary)" }}>{value}</div>
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
  return <div style={{ fontSize: "0.78rem", color: "var(--text-dim)", padding: "10px 4px", textAlign: "center" }}>{children}</div>;
}
