"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot, RefreshCw } from "lucide-react";

import { api, type LongTermIdea, type PortfolioSummary, type ResearchAggregatePerformance, type ResearchCoverageResponse, type RunningTradeMonitorItem, type SwingIdea } from "@/lib/api";

function formatScanAge(isoTime: string | null): { label: string; stale: boolean } {
  if (!isoTime) return { label: "Never", stale: true };
  const diff = Date.now() - new Date(isoTime + "Z").getTime();
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
  const [runningScan, setRunningScan] = useState<"swing" | "longterm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanNotice, setScanNotice] = useState<string | null>(null);
  const [lastSwingScan, setLastSwingScan] = useState<string | null>(null);
  const [lastLongtermScan, setLastLongtermScan] = useState<string | null>(null);

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

  const runScan = useCallback(
    async (scan: "swing" | "longterm") => {
      try {
        setError(null);
        setScanNotice(null);
        setRunningScan(scan);
        const res =
          scan === "swing" ? await api.runSwingScan() : await api.runLongtermScan();
        const note = res.summary || res.message;
        if (res.ok && note) {
          setScanNotice(note);
        } else if (!res.ok) {
          setError((res.message as string | undefined) || `${scan} scan failed`);
        }
        await refresh();
      } catch (err) {
        const msg = err instanceof Error ? err.message : `Unable to run ${scan} scan right now.`;
        setError(msg);
      } finally {
        setRunningScan(null);
      }
    },
    [refresh]
  );

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
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
              return (
                <p style={{ margin: "2px 0 0", fontSize: "0.7rem", color: swingAge.stale || ltAge.stale ? "var(--warning, #f59e0b)" : "var(--text-secondary)" }}>
                  Last scan — Swing: {swingAge.label} · Long-term: {ltAge.label}
                  {(swingAge.stale || ltAge.stale) && " (auto-refresh in progress)"}
                </p>
              );
            })()}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn-accent"
            onClick={() => runScan("swing")}
            disabled={runningScan !== null}
            style={{ fontSize: "0.78rem", padding: "6px 12px", opacity: runningScan && runningScan !== "swing" ? 0.7 : 1 }}
          >
            {runningScan === "swing" ? "Running..." : "Run Swing Scan"}
          </button>
          <button
            className="btn-accent"
            onClick={() => runScan("longterm")}
            disabled={runningScan !== null}
            style={{ fontSize: "0.78rem", padding: "6px 12px", opacity: runningScan && runningScan !== "longterm" ? 0.7 : 1 }}
          >
            {runningScan === "longterm" ? "Running..." : "Run Long-Term Scan"}
          </button>
          <button className="btn-accent" onClick={refresh} style={{ fontSize: "0.78rem", padding: "6px 14px" }}>
            <RefreshCw size={12} style={{ display: "inline", marginRight: 6 }} />
            Refresh
          </button>
        </div>
      </div>

      {error && <div className="glass" style={{ padding: 12, color: "var(--danger)" }}>{error}</div>}
      {scanNotice && !error && (
        <div className="glass" style={{ padding: 12, color: "var(--accent)", border: "1px solid rgba(0,212,255,0.25)" }}>
          {scanNotice}
        </div>
      )}
      {loading && <div className="glass" style={{ padding: 12, color: "var(--text-secondary)" }}>Loading research data...</div>}

      <ResearchCoverageCard coverage={coverage} />
      <PerformanceOverview data={perf} />

      {/* ── SECTION 1: LIVE PORTFOLIO ─────────────────────────── */}
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

      {/* ── SECTION 2: NEW OPPORTUNITIES (Discovery Feed) ───── */}
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

      <RunningTradesMonitor items={running} />
    </div>
  );
}
