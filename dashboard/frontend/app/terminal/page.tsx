"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { RefreshCw, Sparkles, AlertTriangle, TrendingUp, TrendingDown } from "lucide-react";
import { api, type ResearchDecisionFeedResponse } from "@/lib/api";

import OpportunityCard from "./_components/OpportunityCard";
import TradeExplanationDrawer from "./_components/TradeExplanationDrawer";
import SmartWatchlistPanel from "./_components/SmartWatchlistPanel";
import AdvancedFilterBar, { DEFAULT_FILTERS, type FilterState } from "./_components/AdvancedFilterBar";
import DiscoveryFeed from "./_components/DiscoveryFeed";
import AISummaryPanel from "./_components/AISummaryPanel";
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
        bestTrade={summary?.best_opportunity ?? null}
        dailyPnl={dailyPnl}
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

// ─── Compact sticky Hero ─────────────────────────────────────────────────────

type BestTrade = { symbol: string; direction: string; probability: number; action?: string | null; rr?: number | null };
type DailyPnLData = { realized_r: number; wins: number; losses: number; win_rate: number; streak: number; total: number };

function Hero({
  stats,
  loading,
  refreshing,
  onRefresh,
  generatedAt,
  liveStatus,
  bestTrade,
  dailyPnl,
  alertsBell,
}: {
  stats: { total: number; bullish: number; bearish: number; apex: number };
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  generatedAt?: string;
  liveStatus?: "connecting" | "live" | "polling" | "offline";
  bestTrade?: BestTrade | null;
  dailyPnl?: DailyPnLData | null;
  alertsBell?: React.ReactNode;
}) {
  const updated = generatedAt ? new Date(generatedAt) : null;
  const updatedLabel = updated
    ? updated.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
    : loading ? "…" : "—";

  const LIVE_CFG = {
    live:       { color: "#00e096", label: "LIVE",       glow: true  },
    polling:    { color: "#ffa502", label: "POLLING",    glow: false },
    connecting: { color: "#00d4ff", label: "SYNCING",   glow: false },
    offline:    { color: "#ff4757", label: "OFFLINE",   glow: false },
  } as const;
  const lc = liveStatus ? LIVE_CFG[liveStatus] : null;

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 100,
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        background: "linear-gradient(135deg, rgba(8,13,26,0.93) 0%, rgba(12,20,40,0.90) 100%)",
        border: "1px solid var(--border)",
        borderRadius: 14,
        marginBottom: 16,
        overflow: "hidden",
      }}
    >
      {/* Left accent bar */}
      <div
        aria-hidden
        style={{
          position: "absolute", left: 0, top: 0, bottom: 0, width: 3,
          background: "linear-gradient(180deg, #00d4ff 0%, #00e096 100%)",
          borderRadius: "14px 0 0 14px",
        }}
      />

      {/* Single scrollable row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          height: 56,
          padding: "0 14px 0 18px",
          gap: 0,
          overflowX: "auto",
          overflowY: "hidden",
          scrollbarWidth: "none",
          msOverflowStyle: "none" as React.CSSProperties["msOverflowStyle"],
        }}
      >
        {/* Brand + live status */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <Sparkles size={13} color="#00d4ff" />
          <span style={{ fontSize: "0.68rem", fontWeight: 800, letterSpacing: 1.1, color: "var(--text-primary)", whiteSpace: "nowrap" }}>
            TERMINAL
          </span>
          {lc && (
            <div
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 7px", borderRadius: 999,
                background: `${lc.color}18`, border: `1px solid ${lc.color}50`,
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  width: 5, height: 5, borderRadius: "50%", display: "block",
                  background: lc.color,
                  boxShadow: lc.glow ? `0 0 6px ${lc.color}` : "none",
                  animation: lc.glow ? "pulse 1.6s infinite" : undefined,
                }}
              />
              <span style={{ fontSize: "0.52rem", fontWeight: 800, letterSpacing: 0.8, color: lc.color }}>{lc.label}</span>
            </div>
          )}
        </div>

        <HeroDivider />

        {/* Best trade highlight */}
        {bestTrade ? (
          <BestTradeChip trade={bestTrade} />
        ) : (
          <span style={{ fontSize: "0.62rem", color: "var(--text-dim)", whiteSpace: "nowrap", flexShrink: 0 }}>
            {loading ? "Scanning…" : "No top trade yet"}
          </span>
        )}

        <HeroDivider />

        {/* Daily PnL strip */}
        {dailyPnl && dailyPnl.total > 0 ? (
          <DailyPnLStrip data={dailyPnl} />
        ) : (
          <span style={{ fontSize: "0.62rem", color: "var(--text-dim)", whiteSpace: "nowrap", flexShrink: 0 }}>No trades today</span>
        )}

        <HeroDivider />

        {/* Setups count */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          <span style={{ fontSize: "0.62rem", color: "var(--text-dim)", whiteSpace: "nowrap" }}>{stats.total} setups</span>
          {stats.apex > 0 && (
            <span
              style={{
                fontSize: "0.55rem", fontWeight: 700, padding: "1px 6px", borderRadius: 999,
                background: "rgba(255,165,2,0.15)", color: "#ffa502",
                border: "1px solid rgba(255,165,2,0.35)", whiteSpace: "nowrap",
              }}
            >
              {stats.apex} A+
            </span>
          )}
        </div>

        {/* Push actions to far right */}
        <div style={{ flex: 1, minWidth: 12 }} />

        {/* Right actions */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          <span style={{ fontSize: "0.58rem", color: "var(--text-dim)", whiteSpace: "nowrap" }}>{updatedLabel}</span>
          {alertsBell}
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="Refresh"
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 32, height: 32, borderRadius: 8,
              border: "1px solid var(--border)",
              background: "rgba(255,255,255,0.04)",
              color: "var(--accent)",
              cursor: refreshing ? "wait" : "pointer",
              opacity: refreshing ? 0.5 : 1,
              flexShrink: 0,
            }}
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
          </button>
        </div>
      </div>
    </header>
  );
}

function HeroDivider() {
  return <div style={{ width: 1, height: 28, background: "var(--border)", opacity: 0.6, flexShrink: 0, margin: "0 14px" }} />;
}

function BestTradeChip({ trade }: { trade: BestTrade }) {
  const isLong = (trade.direction ?? "").toUpperCase() === "LONG";
  const dirColor = isLong ? "#00e096" : "#ff4757";
  const DirIcon = isLong ? TrendingUp : TrendingDown;
  const actionColors: Record<string, string> = {
    "STRONG BUY": "#00e096",
    BUY: "#00d4ff",
    WATCH: "#ffa502",
    AVOID: "#ff4757",
  };
  const aColor = trade.action ? (actionColors[trade.action] ?? "#8899bb") : null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 7, flexShrink: 0 }}>
      <span style={{ fontSize: "0.52rem", fontWeight: 700, letterSpacing: 0.8, color: "var(--text-dim)", textTransform: "uppercase", whiteSpace: "nowrap" }}>Best</span>
      <span style={{ fontSize: "0.82rem", fontWeight: 800, color: "var(--text-primary)", whiteSpace: "nowrap" }}>{trade.symbol}</span>
      <DirIcon size={12} color={dirColor} />
      <span style={{ fontSize: "0.68rem", fontWeight: 700, color: "var(--text-secondary)", fontFamily: "ui-monospace, monospace", whiteSpace: "nowrap" }}>{trade.probability}%</span>
      {aColor && trade.action && (
        <span
          style={{
            fontSize: "0.55rem", fontWeight: 800, letterSpacing: 0.6,
            padding: "2px 7px", borderRadius: 999,
            background: `${aColor}18`, color: aColor, border: `1px solid ${aColor}55`,
            whiteSpace: "nowrap",
          }}
        >
          {trade.action}
        </span>
      )}
      {trade.rr != null && (
        <span style={{ fontSize: "0.6rem", color: "var(--text-dim)", fontFamily: "ui-monospace, monospace", whiteSpace: "nowrap" }}>
          {Number(trade.rr).toFixed(1)}R
        </span>
      )}
    </div>
  );
}

function DailyPnLStrip({ data }: { data: DailyPnLData }) {
  const rColor = data.realized_r > 0 ? "#00e096" : data.realized_r < 0 ? "#ff4757" : "#8899bb";
  const wrColor = data.win_rate >= 60 ? "#00e096" : data.win_rate >= 40 ? "#ffa502" : "#ff4757";
  const streak = data.streak;
  const streakLabel = streak > 1 ? `🔥${streak}` : streak < -1 ? `${Math.abs(streak)}↓` : null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
      <span style={{ fontSize: "0.72rem", fontWeight: 800, color: rColor, fontFamily: "ui-monospace, monospace", whiteSpace: "nowrap" }}>
        {data.realized_r > 0 ? "+" : ""}{data.realized_r.toFixed(1)}R
      </span>
      <span style={{ fontSize: "0.62rem", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>{data.wins}W/{data.losses}L</span>
      <span style={{ fontSize: "0.62rem", fontWeight: 700, color: wrColor, whiteSpace: "nowrap" }}>{data.win_rate.toFixed(0)}%</span>
      {streakLabel && (
        <span style={{ fontSize: "0.62rem", color: streak > 0 ? "#ffa502" : "#ff4757", whiteSpace: "nowrap" }}>{streakLabel}</span>
      )}
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
