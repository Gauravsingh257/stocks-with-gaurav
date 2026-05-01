"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { RefreshCw, Radio, Sparkles, AlertTriangle } from "lucide-react";
import { api, type ResearchDecisionFeedResponse } from "@/lib/api";

import OpportunityCard from "./_components/OpportunityCard";
import TradeExplanationDrawer from "./_components/TradeExplanationDrawer";
import SmartWatchlistPanel from "./_components/SmartWatchlistPanel";
import AdvancedFilterBar, { DEFAULT_FILTERS, type FilterState } from "./_components/AdvancedFilterBar";
import DiscoveryFeed from "./_components/DiscoveryFeed";
import AISummaryPanel from "./_components/AISummaryPanel";
import PnLTracker from "./_components/PnLTracker";
import AlertsBell from "./_components/AlertsBell";
import { liveTradeToOpportunity, toOpportunities, type Opportunity } from "./_lib/opportunity";
import { useLiveTrades } from "./_lib/useLiveTrades";
import { useTerminalSummary } from "./_lib/useTerminalSummary";

const REFRESH_MS = 60_000;
const STORAGE_KEY = "terminal:watchlist:v1";

export default function TerminalPage() {
  const [feed, setFeed] = useState<ResearchDecisionFeedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [activeOpp, setActiveOpp] = useState<Opportunity | null>(null);
  const [watchedIds, setWatchedIds] = useState<string[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  // Hydrate watchlist
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) setWatchedIds(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(watchedIds));
    } catch {
      /* ignore */
    }
  }, [watchedIds]);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await api.researchDecisionFeed(40, 1);
      setFeed(res);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load opportunities");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  // Phase 2 — live trades from /ws/trades (with /api/trades fallback)
  const live = useLiveTrades();
  const liveOpps = useMemo(() => live.trades.map(liveTradeToOpportunity), [live.trades]);
  // Phase 3+4 — AI summary + daily PnL + markTaken
  const { summary, dailyPnl, markTaken } = useTerminalSummary();

  const handleMarkTaken = useCallback(
    async (opp: Opportunity) => {
      await markTaken(opp.symbol);
    },
    [markTaken],
  );

  const allOpps = useMemo(() => {
    const final = feed ? toOpportunities(feed.final_trades) : [];
    const watch = feed ? toOpportunities(feed.watchlist) : [];
    const disc = feed ? toOpportunities(feed.discovery) : [];
    const seen = new Set<string>();
    const merged: Opportunity[] = [];
    // Live takes precedence so freshly tapped/triggered trades surface first
    for (const arr of [liveOpps, final, watch, disc]) {
      for (const o of arr) {
        if (seen.has(o.symbol)) continue;
        seen.add(o.symbol);
        merged.push(o);
      }
    }
    return merged;
  }, [feed, liveOpps]);

  const finalOpps = useMemo(() => {
    const fromFeed = feed ? toOpportunities(feed.final_trades) : [];
    const seen = new Set<string>();
    const out: Opportunity[] = [];
    for (const arr of [liveOpps, fromFeed]) {
      for (const o of arr) {
        if (seen.has(o.symbol)) continue;
        seen.add(o.symbol);
        out.push(o);
      }
    }
    return out;
  }, [feed, liveOpps]);
  const watchOpps = useMemo(() => toOpportunities(feed?.watchlist), [feed]);
  const discoveryOpps = useMemo(() => toOpportunities(feed?.discovery), [feed]);

  const filteredHero = useMemo(() => {
    return finalOpps.filter((o) => matchFilter(o, filters));
  }, [finalOpps, filters]);

  const userWatchlist = useMemo(() => {
    return allOpps.filter((o) => watchedIds.includes(o.id));
  }, [allOpps, watchedIds]);

  const smartZones = useMemo(() => {
    // approaching zones from server-side watchlist + discovery
    const merged = [...watchOpps, ...discoveryOpps];
    const seen = new Set<string>();
    return merged.filter((o) => {
      if (seen.has(o.id)) return false;
      seen.add(o.id);
      return true;
    });
  }, [watchOpps, discoveryOpps]);

  const toggleWatch = useCallback((opp: Opportunity) => {
    setWatchedIds((prev) => (prev.includes(opp.id) ? prev.filter((x) => x !== opp.id) : [...prev, opp.id]));
  }, []);

  const stats = useMemo(() => {
    return {
      total: allOpps.length,
      bullish: allOpps.filter((o) => o.direction === "BUY").length,
      bearish: allOpps.filter((o) => o.direction === "SELL").length,
      apex: allOpps.filter((o) => o.grade === "A+").length,
    };
  }, [allOpps]);

  return (
    <div style={{ minHeight: "100vh", padding: "28px 24px 56px", maxWidth: 1640, margin: "0 auto" }}>
      <Hero
        stats={stats}
        loading={loading}
        refreshing={refreshing}
        onRefresh={load}
        generatedAt={feed?.generated_at}
        liveStatus={live.status}
        pnlTracker={<PnLTracker data={dailyPnl} />}
        alertsBell={<AlertsBell />}
      />

      <AISummaryPanel
        data={summary}
        loading={loading}
        onPickSymbol={(sym) => {
          const match = allOpps.find((o) => o.symbol === sym);
          if (match) setActiveOpp(match);
        }}
      />

      <div style={{ marginTop: 18, marginBottom: 18 }}>
        <AdvancedFilterBar value={filters} onChange={setFilters} total={finalOpps.length} visible={filteredHero.length} />
      </div>

      {error && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: 12,
            marginBottom: 18,
            background: "rgba(255,71,87,0.1)",
            border: "1px solid rgba(255,71,87,0.3)",
            borderRadius: 12,
            color: "#ff4757",
            fontSize: "0.78rem",
          }}
        >
          <AlertTriangle size={16} />
          {error}
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 360px)",
          gap: 22,
          alignItems: "start",
        }}
        className="terminal-grid"
      >
        <main>
          <SectionTitle
            kicker="Live Trade Opportunities"
            title="Decision-ready setups"
            subtitle="Hand-picked by the SMC engine across multi-timeframe structure, liquidity, and order block alignment."
          />

          {loading && finalOpps.length === 0 ? (
            <CardSkeletonGrid />
          ) : filteredHero.length === 0 ? (
            <EmptyHero />
          ) : (
            <motion.div
              layout
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
                gap: 16,
              }}
            >
              <AnimatePresence mode="popLayout">
                {filteredHero.map((opp, idx) => (
                  <OpportunityCard
                    key={opp.id}
                    opp={opp}
                    index={idx}
                    onView={setActiveOpp}
                    onWatch={toggleWatch}
                    onMarkTaken={handleMarkTaken}
                    watched={watchedIds.includes(opp.id)}
                  />
                ))}
              </AnimatePresence>
            </motion.div>
          )}

          <div style={{ marginTop: 36 }}>
            <SectionTitle
              kicker="Discovery Feed"
              title="What just happened on the tape"
              subtitle="Realtime signals: new setups, sweeps, and approaching entry zones."
            />
            <DiscoveryFeed items={smartZones} onSelect={setActiveOpp} />
          </div>
        </main>

        <aside style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 16 }}>
          <SmartWatchlistPanel
            items={smartZones.slice(0, 10)}
            onSelect={setActiveOpp}
            emptyHint="Engine sees no zones nearing entry yet."
          />
          <SmartWatchlistPanel
            items={userWatchlist}
            onSelect={setActiveOpp}
            onRemove={toggleWatch}
            emptyHint="Bookmark setups from the live feed to track them here."
          />
        </aside>
      </div>

      <TradeExplanationDrawer opp={activeOpp} onClose={() => setActiveOpp(null)} />

      <style jsx global>{`
        @media (max-width: 1100px) {
          .terminal-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  );
}

function matchFilter(o: Opportunity, f: FilterState): boolean {
  if (f.setups.length > 0 && !f.setups.includes(o.setup)) return false;
  if (f.direction !== "all" && o.direction !== f.direction) return false;
  if (f.query.trim()) {
    const q = f.query.trim().toLowerCase();
    if (!o.symbol.toLowerCase().includes(q)) return false;
  }
  if (f.risk === "conservative" && o.grade === "C") return false;
  // Strategy mode: best-effort using expected_holding_period
  if (f.strategy !== "all") {
    const horizon = (o.raw.expected_holding_period ?? "").toLowerCase();
    if (f.strategy === "intraday" && horizon && !/intraday|day|hour/.test(horizon)) return false;
    if (f.strategy === "swing" && horizon && !/swing|day|week/.test(horizon)) return false;
  }
  return true;
}

function Hero({
  stats,
  loading,
  refreshing,
  onRefresh,
  generatedAt,
  liveStatus,
  pnlTracker,
  alertsBell,
}: {
  stats: { total: number; bullish: number; bearish: number; apex: number };
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  generatedAt?: string;
  liveStatus?: "connecting" | "live" | "polling" | "offline";
  pnlTracker?: React.ReactNode;
  alertsBell?: React.ReactNode;
}) {
  const updated = generatedAt ? new Date(generatedAt) : null;
  const updatedLabel = updated ? updated.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : loading ? "Syncing…" : "—";

  return (
    <header
      style={{
        position: "relative",
        padding: "26px 28px",
        borderRadius: 22,
        background:
          "radial-gradient(circle at 12% 0%, rgba(0,212,255,0.18), transparent 60%), radial-gradient(circle at 88% 100%, rgba(0,224,150,0.12), transparent 55%), linear-gradient(160deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01))",
        border: "1px solid var(--border)",
        overflow: "hidden",
      }}
    >
      <div aria-hidden style={{ position: "absolute", inset: 0, background: "url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2240%22 height=%2240%22><path d=%22M0 39.5H40M39.5 0V40%22 stroke=%22rgba(255,255,255,0.025)%22 stroke-width=%221%22 fill=%22none%22/></svg>')", opacity: 0.6, pointerEvents: "none" }} />
      <div style={{ position: "relative", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 24, flexWrap: "wrap" }}>
        <div style={{ maxWidth: 720 }}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.62rem", fontWeight: 700, letterSpacing: 1.2, color: "var(--accent)", background: "rgba(0,212,255,0.12)", padding: "4px 10px", border: "1px solid var(--accent-dim)", borderRadius: 999 }}>
            <Sparkles size={12} /> AI TRADE OPPORTUNITY TERMINAL
          </div>
          <h1 style={{ margin: "12px 0 6px", fontSize: "clamp(1.6rem, 2.6vw, 2.2rem)", fontWeight: 850, color: "var(--text-primary)", letterSpacing: -0.4 }}>
            Live Trade Opportunities
          </h1>
          <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: "0.92rem", lineHeight: 1.55, maxWidth: 640 }}>
            Not a screener. A decision engine. Every card is a complete plan — order block, fair value gap, liquidity sweep,
            and structural confirmation, scored and ready for action.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {alertsBell}
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "8px 14px",
                borderRadius: 10,
                border: "1px solid var(--accent)",
                background: "linear-gradient(135deg, rgba(0,212,255,0.2), rgba(0,212,255,0.05))",
                color: "var(--accent)",
                fontSize: "0.74rem",
                fontWeight: 700,
                cursor: refreshing ? "wait" : "pointer",
                opacity: refreshing ? 0.6 : 1,
              }}
            >
              <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} /> Refresh
            </button>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.66rem", color: "var(--text-dim)" }}>
            <Radio size={11} color="#00e096" /> Updated {updatedLabel}
          </div>
          {liveStatus && <LivePill status={liveStatus} />}
          {pnlTracker}
        </div>
      </div>

      <div style={{ position: "relative", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginTop: 22 }}>
        <Stat label="Total Setups" value={stats.total} accent="var(--accent)" />
        <Stat label="Bullish" value={stats.bullish} accent="#00e096" />
        <Stat label="Bearish" value={stats.bearish} accent="#ff4757" />
        <Stat label="A+ Grade" value={stats.apex} accent="#ffa502" />
      </div>
    </header>
  );
}

function LivePill({ status }: { status: "connecting" | "live" | "polling" | "offline" }) {
  const map = {
    live: { color: "#00e096", label: "LIVE", dot: true },
    polling: { color: "#ffa502", label: "POLLING", dot: false },
    connecting: { color: "#00d4ff", label: "CONNECTING", dot: false },
    offline: { color: "#ff4757", label: "OFFLINE", dot: false },
  } as const;
  const m = map[status];
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: "0.6rem",
        fontWeight: 700,
        letterSpacing: 1,
        color: m.color,
        background: `${m.color}1f`,
        border: `1px solid ${m.color}66`,
        padding: "3px 8px",
        borderRadius: 999,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: m.color,
          boxShadow: m.dot ? `0 0 8px ${m.color}` : "none",
          animation: m.dot ? "pulse 1.6s infinite" : undefined,
        }}
      />
      {m.label}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.03)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        padding: "10px 14px",
      }}
    >
      <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", letterSpacing: 0.6, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "1.4rem", fontWeight: 800, color: accent, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{value}</div>
    </div>
  );
}

function SectionTitle({ kicker, title, subtitle }: { kicker: string; title: string; subtitle?: string }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1.2 }}>{kicker}</div>
      <h2 style={{ margin: "2px 0 4px", fontSize: "1.2rem", fontWeight: 800, color: "var(--text-primary)" }}>{title}</h2>
      {subtitle && <p style={{ margin: 0, fontSize: "0.78rem", color: "var(--text-secondary)" }}>{subtitle}</p>}
    </div>
  );
}

function CardSkeletonGrid() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16 }}>
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          style={{
            height: 320,
            borderRadius: 18,
            background: "linear-gradient(110deg, rgba(255,255,255,0.03) 30%, rgba(255,255,255,0.07) 50%, rgba(255,255,255,0.03) 70%)",
            backgroundSize: "200% 100%",
            border: "1px solid var(--border)",
            animation: "shimmer 1.6s linear infinite",
          }}
        />
      ))}
      <style jsx>{`
        @keyframes shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  );
}

function EmptyHero() {
  return (
    <div
      style={{
        padding: "40px 24px",
        textAlign: "center",
        borderRadius: 18,
        background: "rgba(255,255,255,0.025)",
        border: "1px dashed var(--border)",
        color: "var(--text-secondary)",
      }}
    >
      <Sparkles size={22} color="var(--accent)" />
      <div style={{ marginTop: 10, fontSize: "0.92rem", fontWeight: 700, color: "var(--text-primary)" }}>No final-grade setups match your filters</div>
      <div style={{ marginTop: 4, fontSize: "0.78rem" }}>Loosen the strategy or setup filter, or check back as the next scan completes.</div>
    </div>
  );
}
