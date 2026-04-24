"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, RefreshCw, Zap, TrendingUp } from "lucide-react";
import { StaggerContainer, StaggerItem } from "@/components/MotionWrappers";

import { api, type LongTermIdea, type PortfolioSummary, type ResearchAggregatePerformance, type ResearchCoverageResponse, type RunningTradeMonitorItem, type SwingIdea } from "@/lib/api";

function formatScanAge(isoTime: string | null): { label: string; stale: boolean } {
  if (!isoTime) return { label: "Never", stale: true };
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

const SCAN_BTN: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", gap: 6,
  padding: "6px 14px", borderRadius: 8, fontWeight: 600, fontSize: "0.72rem",
  cursor: "pointer", border: "1px solid", transition: "opacity 0.2s",
};

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
  const [scanning, setScanning] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

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
    setLastRefresh(new Date());
  }, []);

  const isEmpty = swing.length === 0 && longterm.length === 0 && running.length === 0 && !portfolio;
  const pollInterval = isEmpty ? 120_000 : 30_000;

  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") refresh();
    }, pollInterval);
    return () => clearInterval(t);
  }, [refresh, pollInterval]);

  const triggerScan = useCallback(async (horizon: "swing" | "longterm") => {
    setScanning(horizon);
    try {
      if (horizon === "swing") await api.runSwingScan();
      else await api.runLongtermScan();
    } catch {
      // scan trigger failed — will show in scan-status
    }
    // poll aggressively for a short window to pick up results
    const polls = [5_000, 15_000, 30_000, 60_000, 90_000];
    for (const delay of polls) {
      setTimeout(() => refresh(), delay);
    }
    setTimeout(() => setScanning(null), 5_000);
  }, [refresh]);

  const scanButton = useCallback((horizon: "swing" | "longterm", variant: "accent" | "warning" = "accent") => {
    const isActive = scanning === horizon;
    const color = variant === "accent" ? "#00d4ff" : "#f59e0b";
    return (
      <button
        onClick={() => triggerScan(horizon)}
        disabled={isActive || scanning !== null}
        style={{
          ...SCAN_BTN,
          background: `${color}18`,
          borderColor: `${color}55`,
          color,
          opacity: isActive ? 0.6 : 1,
        }}
      >
        {isActive ? (
          <RefreshCw size={12} className="animate-spin" />
        ) : (
          <Zap size={12} />
        )}
        {isActive ? "Scanning..." : `Scan ${horizon === "swing" ? "Swing" : "Long-Term"}`}
      </button>
    );
  }, [scanning, triggerScan]);

  // ── Header scan age info ──
  const scanAgeInfo = useMemo(() => {
    const swingAge = formatScanAge(lastSwingScan);
    const ltAge = formatScanAge(lastLongtermScan);
    const slotsFull = longtermSlotStatus?.slots_full;
    const ltLabel = slotsFull
      ? `all ${longtermSlotStatus!.max} slots occupied`
      : ltAge.label;
    const ltStale = ltAge.stale && !slotsFull;
    return { swingAge, ltAge, ltLabel, ltStale, slotsFull };
  }, [lastSwingScan, lastLongtermScan, longtermSlotStatus]);

  return (
    <StaggerContainer stagger={0.08} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ── HEADER ──────────────────────────────────────────────── */}
      <StaggerItem>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "rgba(0,212,255,0.1)", border: "1px solid rgba(0,212,255,0.2)", display: "grid", placeItems: "center" }}>
            <Bot size={17} color="var(--accent)" />
          </div>
          <div>
            <h1 className="m-0 text-xl md:text-2xl lg:text-3xl font-bold">AI Research Center</h1>
            <p style={{ margin: "2px 0 0", color: "var(--text-secondary)", fontSize: "0.8rem" }}>
              Swing ideas, long-term theses, and running trade intelligence
            </p>
            <p style={{ margin: "2px 0 0", fontSize: "0.7rem", color: scanAgeInfo.swingAge.stale || scanAgeInfo.ltStale ? "var(--warning, #f59e0b)" : "var(--text-secondary)" }}>
              Last scan — Swing: {scanAgeInfo.swingAge.label} · Long-term: {scanAgeInfo.ltLabel}
              {(scanAgeInfo.swingAge.stale || scanAgeInfo.ltStale) && !scanning && " (auto-refresh in progress)"}
              {scanning && " (manual scan running...)"}
            </p>
            {lastRefresh && (
              <p style={{ margin: "1px 0 0", fontSize: "0.62rem", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#00d18c", display: "inline-block", animation: "pulse 2s infinite" }} />
                Data refreshed {lastRefresh.toLocaleTimeString()} · Auto-updates every {isEmpty ? "2m" : "30s"}
              </p>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {scanButton("swing")}
          {scanButton("longterm", "warning")}
        </div>
      </div>
      </StaggerItem>

      {/* ── ERROR / LOADING ────────────────────────────────────── */}
      {error && (
        <StaggerItem>
          <div className="glass" style={{ padding: 12, color: "var(--danger)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <span>{error}</span>
            <button
              onClick={refresh}
              style={{
                ...SCAN_BTN,
                background: "rgba(255,77,109,0.12)",
                borderColor: "rgba(255,77,109,0.3)",
                color: "#ff4d6d",
                flexShrink: 0,
              }}
            >
              <RefreshCw size={12} /> Retry
            </button>
          </div>
        </StaggerItem>
      )}
      {loading && <StaggerItem><div className="glass" style={{ padding: 12, color: "var(--text-secondary)" }}>Loading research data...</div></StaggerItem>}

      {/* ── ONBOARDING CARD (shown when everything is empty) ──── */}
      {isEmpty && !loading && (
        <StaggerItem>
          <div className="glass" style={{ padding: "32px 24px", textAlign: "center" }}>
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 16 }}>
              <div style={{ width: 56, height: 56, borderRadius: 14, background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.15)", display: "grid", placeItems: "center" }}>
                <TrendingUp size={26} color="var(--accent)" />
              </div>
            </div>
            <h2 style={{ margin: "0 0 8px", fontSize: "1.2rem" }}>Welcome to the AI Research Center</h2>
            <p style={{ color: "var(--text-secondary)", maxWidth: 560, margin: "0 auto 8px", fontSize: "0.85rem", lineHeight: 1.6 }}>
              This platform uses <strong>Smart Money Concepts</strong> (SMC) — order blocks, fair value gaps, and institutional flow analysis — combined with fundamental and sentiment scoring to identify high-conviction trade setups across 1800+ NSE stocks.
            </p>
            <p style={{ color: "var(--text-dim)", fontSize: "0.78rem", marginBottom: 20 }}>
              No scan has been run yet. Start a scan to analyze the market — it takes 1–3 minutes.
            </p>
            <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
              {scanButton("swing")}
              {scanButton("longterm", "warning")}
            </div>
          </div>
        </StaggerItem>
      )}

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
        <SwingIdeasTable items={swing} slotInfo={`${swing.length} Ideas`} onScan={() => triggerScan("swing")} scanning={scanning === "swing"} />
        <LongTermIdeasCard items={longterm} slotInfo={`${longterm.length} Ideas`} onScan={() => triggerScan("longterm")} scanning={scanning === "longterm"} />
      </div>
      </StaggerItem>

      <StaggerItem><RunningTradesMonitor items={running} /></StaggerItem>
    </StaggerContainer>
  );
}
