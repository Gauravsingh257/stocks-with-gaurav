"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot } from "lucide-react";
import { FadeIn, StaggerContainer, StaggerItem, GlassCard } from "@/components/MotionWrappers";

import { api, type LongTermIdea, type PortfolioSummary, type ResearchAggregatePerformance, type ResearchCoverageResponse, type RunningTradeMonitorItem, type SwingIdea } from "@/lib/api";

function formatScanAge(isoTime: string | null): { label: string; stale: boolean } {
  if (!isoTime) return { label: "Never", stale: true };
  // Defensively ensure exactly one trailing Z so naive UTC strings parse correctly
  const normalized = isoTime.endsWith("Z") ? isoTime : isoTime + "Z";
  const diff = Date.now() - new Date(normalized).getTime();
  const hours = Math.floor(diff / 3_600_000);
  if (hours < 1) return { label: "< 1h ago", stale: false };
  if (hours < 24) return { label: `${hours}h ago`, stale: hours > 12 };
  const days = Math.floor(hours / 24);
  return { label: `${days}d ago`, stale: true };
}
import { LongTermIdeasCard } from "./LongTermIdeasCard";
import { PerformanceOverview } from "./PerformanceOverview";
import { PortfolioSection } from "./PortfolioSection";
import { ResearchCoverageCard } from "./ResearchCoverageCard";
import { RunningTradesMonitor } from "./RunningTradesMonitor";
import { SwingIdeasTable } from "./SwingIdeasTable";

export default function ResearchPage() {
  const [swing, setSwing] = useState<SwingIdea[]>([]);
  const [longterm, setLongterm] = useState<LongTermIdea[]>([]);
  const [running, setRunning] = useState<RunningTradeMonitorItem[]>([]);
  const [coverage, setCoverage] = useState<ResearchCoverageResponse | null>(null);
  const [perf, setPerf] = useState<ResearchAggregatePerformance | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastSwingScan, setLastSwingScan] = useState<string | null>(null);
  const [lastLongtermScan, setLastLongtermScan] = useState<string | null>(null);
  const [longtermSlotStatus, setLongtermSlotStatus] = useState<{ occupied: number; max: number; slots_full: boolean } | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      api.swingResearch(10),
      api.longtermResearch(10),
      api.runningTradesResearch(40),
      api.researchCoverage(1800),
      api.researchPerformance(),
      api.portfolioSummary(),
    ]);
    const [swingRes, longtermRes, runningRes, coverageRes, perfRes, portfolioRes] = results;
    if (swingRes.status === "fulfilled") {
      setSwing(swingRes.value?.items ?? []);
      setLastSwingScan((swingRes.value as Record<string, unknown>)?.last_scan_time as string | null ?? null);
    }
    if (longtermRes.status === "fulfilled") {
      setLongterm(longtermRes.value?.items ?? []);
      setLastLongtermScan((longtermRes.value as Record<string, unknown>)?.last_scan_time as string | null ?? null);
      setLongtermSlotStatus((longtermRes.value as Record<string, unknown>)?.slot_status as { occupied: number; max: number; slots_full: boolean } | null ?? null);
    }
    if (runningRes.status === "fulfilled") {
      setRunning(runningRes.value?.items ?? []);
    }
    if (coverageRes.status === "fulfilled") {
      setCoverage(coverageRes.value ?? null);
    }
    if (perfRes.status === "fulfilled") {
      setPerf(perfRes.value ?? null);
    }
    if (portfolioRes.status === "fulfilled") {
      setPortfolio(portfolioRes.value ?? null);
    }
    const failed = results.filter((r) => r.status === "rejected").length;
    if (failed > 0) {
      setError("Some data could not be loaded. Run a scan or refresh — backend may still be syncing.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") refresh();
    }, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <StaggerContainer stagger={0.08} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <StaggerItem>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "rgba(0,212,255,0.1)", border: "1px solid rgba(0,212,255,0.2)", display: "grid", placeItems: "center" }}>
            <Bot size={17} color="var(--accent)" />
          </div>
          <div>
            <h1 className="m-0 text-xl md:text-2xl lg:text-3xl font-bold">AI Research Center</h1>
            <p style={{ margin: "2px 0 0", color: "var(--text-secondary)", fontSize: "0.8rem" }}>
              Swing ideas, long-term theses, and running trade intelligence
            </p>
            {(() => {
              const swingAge = formatScanAge(lastSwingScan);
              const ltAge = formatScanAge(lastLongtermScan);
              const slotsFull = longtermSlotStatus?.slots_full;
              const ltLabel = slotsFull
                ? `all ${longtermSlotStatus.max} slots occupied (next scan when one closes)`
                : ltAge.label;
              const ltStale = ltAge.stale && !slotsFull;
              return (
                <p style={{ margin: "2px 0 0", fontSize: "0.7rem", color: swingAge.stale || ltStale ? "var(--warning, #f59e0b)" : "var(--text-secondary)" }}>
                  Last scan — Swing: {swingAge.label} · Long-term: {ltLabel}
                  {(swingAge.stale || ltStale) && " (auto-refresh in progress)"}
                </p>
              );
            })()}
          </div>
        </div>
      </div>
      </StaggerItem>

      {error && <StaggerItem><div className="glass" style={{ padding: 12, color: "var(--danger)" }}>{error}</div></StaggerItem>}
      {loading && <StaggerItem><div className="glass" style={{ padding: 12, color: "var(--text-secondary)" }}>Loading research data...</div></StaggerItem>}

      <StaggerItem><ResearchCoverageCard coverage={coverage} /></StaggerItem>
      <StaggerItem><PerformanceOverview data={perf} /></StaggerItem>

      {/* ── SECTION 1: LIVE PORTFOLIO ─────────────────────────── */}
      <StaggerItem>
      <div style={{ marginTop: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <div style={{ width: 4, height: 24, borderRadius: 2, background: "var(--accent, #00d4ff)" }} />
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>Live Portfolio</h2>
          <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(0,212,255,0.1)", border: "1px solid rgba(0,212,255,0.2)", color: "var(--accent)" }}>
            {portfolio ? `${portfolio.swing.count + portfolio.longterm.count} Active` : "—"}
          </span>
        </div>
        {portfolio ? (
          <>
            <PortfolioSection
              title="Swing Portfolio"
              positions={portfolio.swing.positions}
              count={portfolio.swing.count}
              max={portfolio.swing.max}
              journalStats={portfolio.swing.journal_stats}
              horizon="SWING"
            />
            <PortfolioSection
              title="Long-Term Portfolio"
              positions={portfolio.longterm.positions}
              count={portfolio.longterm.count}
              max={portfolio.longterm.max}
              journalStats={portfolio.longterm.journal_stats}
              horizon="LONGTERM"
            />
          </>
        ) : (
          <div className="glass" style={{ padding: 16, color: "var(--text-secondary)", fontSize: "0.85rem" }}>
            No portfolio data yet. Run a scan to populate.
          </div>
        )}
      </div>
      </StaggerItem>

      {/* ── SECTION 2: NEW OPPORTUNITIES (Discovery Feed) ───── */}
      <StaggerItem>
      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <div style={{ width: 4, height: 24, borderRadius: 2, background: "var(--warning, #f59e0b)" }} />
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>New Opportunities</h2>
          <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.2)", color: "var(--warning, #f59e0b)" }}>
            Discovery Feed
          </span>
        </div>
        <SwingIdeasTable items={swing} slotInfo={`${swing.length} Ideas`} />
        <LongTermIdeasCard items={longterm} slotInfo={`${longterm.length} Ideas`} />
      </div>
      </StaggerItem>

      <StaggerItem><RunningTradesMonitor items={running} /></StaggerItem>
    </StaggerContainer>
  );
}
