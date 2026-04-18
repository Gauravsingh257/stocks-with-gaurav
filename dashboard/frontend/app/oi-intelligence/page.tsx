"use client";
/**
 * /oi-intelligence — Live OI Radar System
 *
 * Aggregates 3 OI modules into a unified visual dashboard.
 * Components split into separate files for maintainability.
 *
 * Features:
 *  - visibilitychange auto-refresh (instant data when returning to tab)
 *  - WebSocket live push (every 10s from backend broadcast loop)
 *  - REST polling fallback (every 10s)
 *  - Auto-reconnect WebSocket on disconnect
 */
import { useEffect, useState, useCallback, useRef } from "react";
import { StaggerContainer, StaggerItem } from "@/components/MotionWrappers";
import {
  Eye, Shield, Clock, RefreshCw, Wifi, WifiOff, AlertTriangle, Activity,
} from "lucide-react";
import type { OISnapshot } from "./types";
import { PCRGauge } from "./PCRGauge";
import { OverallBiasCard } from "./OverallBiasCard";
import { UnderlyingSummaryCards } from "./UnderlyingSummaryCards";
import { StrikeHeatmap } from "./StrikeHeatmap";
import { ShortCoveringPanel } from "./ShortCoveringPanel";
import { ExecutionQualityPanel } from "./ExecutionQualityPanel";
import { PCRSparkline, BiasTimeline } from "./HistoryCharts";
import { MarketStatePanel } from "./MarketStatePanel";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

export default function OIIntelligencePage() {
  const [snapshot, setSnapshot] = useState<OISnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ── Fetch via REST ─────────────────────────────────────── */
  const fetchSnapshot = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/api/agents/oi-intelligence`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSnapshot(data);
      setLastUpdate(new Date().toLocaleTimeString());
      setError(null);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  /* ── WebSocket — primary data source (OI pushed every 30s) ─ */
  useEffect(() => {
    const backend = (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim();
    let wsUrl = (process.env.NEXT_PUBLIC_WS_URL || "").trim();
    if (!wsUrl && backend) {
      const wsProto = backend.startsWith("https") ? "wss" : "ws";
      const host = backend.replace(/^https?:\/\//, "").replace(/\/$/, "");
      wsUrl = `${wsProto}://${host}/ws`;
    }
    if (!wsUrl) {
      const hostname = window.location.hostname;
      if (hostname === "localhost" || hostname === "127.0.0.1") {
        wsUrl = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`;
      }
    }
    if (!wsUrl) return;
    let ws: WebSocket;
    let dead = false;
    let failCount = 0;

    function connect() {
      if (dead || failCount >= 3) return;
      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => { setWsConnected(true); failCount = 0; };
      ws.onclose = () => {
        setWsConnected(false);
        failCount += 1;
        const delay = Math.min(3000 * Math.pow(2, failCount - 1), 30000);
        if (!dead && failCount < 3) setTimeout(connect, delay);
      };
      ws.onerror = () => setWsConnected(false);
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "oi_intelligence" && msg.data) {
            setSnapshot(msg.data);
            setLastUpdate(new Date().toLocaleTimeString());
            setError(null);
            setLoading(false);
          }
        } catch { /* ignore non-JSON frames */ }
      };
    }

    connect();
    return () => { dead = true; ws?.close(); };
  }, []);

  /* ── REST Polling — fallback only when WebSocket is down ── */
  useEffect(() => {
    fetchSnapshot(); // always do an immediate load on mount

    if (!autoRefresh) return;

    // Poll at 30s when WS is connected (safety net), 10s when WS is down.
    // This avoids redundant parallel requests when WebSocket is healthy.
    const interval = wsConnected ? 30_000 : 10_000;
    pollRef.current = setInterval(() => {
      // Never poll while the tab is hidden — saves API quota
      if (document.visibilityState !== "hidden") fetchSnapshot();
    }, interval);

    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [autoRefresh, fetchSnapshot, wsConnected]);

  /* ── Visibility Change — instant refresh on tab focus ──── */
  useEffect(() => {
    function handleVisibility() {
      if (document.visibilityState === "visible") fetchSnapshot();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [fetchSnapshot]);

  /* ── Render ─────────────────────────────────────────────── */
  return (
    <StaggerContainer stagger={0.07} className="w-full max-w-screen-2xl mx-auto px-4 md:px-6 lg:px-8 py-6">
      {/* Header */}
      <StaggerItem>
      <div className="flex flex-col md:flex-row md:justify-between md:items-center gap-4 mb-6">
        <div>
          <h1 className="text-lg md:text-xl lg:text-2xl font-extrabold flex items-center gap-2.5 m-0" style={{ color: "var(--text-primary)" }}>
            <Eye size={22} color="var(--accent)" />
            Live OI Radar
          </h1>
          <p style={{ fontSize: "0.78rem", color: "var(--text-secondary)", margin: "4px 0 0" }}>
            Real-time Open Interest intelligence from PCR, Strike Activity &amp; execution quality
          </p>
          {snapshot?.last_update && (
            <p style={{ fontSize: "0.7rem", color: "var(--text-dim)", margin: "2px 0 0" }}>
              <Activity size={9} style={{ display: "inline", marginRight: 3, verticalAlign: "middle" }} />
              Last generated: {new Date(snapshot.last_update).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              {!( snapshot.market_hours ?? snapshot.market_open) && " · Outside market hours — showing last session data"}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* LIVE / SNAPSHOT indicator based on market hours */}
          {snapshot && (() => {
            const isLive = snapshot.market_hours ?? snapshot.market_open ?? false;
            return (
              <span style={{
                padding: "2px 10px",
                borderRadius: 20,
                fontSize: "0.68rem",
                fontWeight: 700,
                letterSpacing: "0.05em",
                background: isLive ? "rgba(0,209,140,0.12)" : "rgba(240,192,96,0.12)",
                border: `1px solid ${isLive ? "rgba(0,209,140,0.4)" : "rgba(240,192,96,0.4)"}`,
                color: isLive ? "#00d18c" : "#f0c060",
              }}>
                {isLive ? "LIVE DATA" : "SNAPSHOT"}
              </span>
            );
          })()}

          <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.7rem", color: wsConnected ? "var(--success)" : "var(--text-dim)" }}>
            {wsConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
            {wsConnected ? "WS" : "REST"}
          </div>

          {lastUpdate && (
            <span style={{ fontSize: "0.7rem", color: "var(--text-dim)" }}>
              <Clock size={10} style={{ display: "inline", marginRight: 3, verticalAlign: "middle" }} />
              {lastUpdate}
            </span>
          )}

          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`badge ${autoRefresh ? "badge-live" : "badge-neutral"}`}
            style={{ cursor: "pointer", padding: "4px 10px" }}
          >
            <RefreshCw size={10} className={autoRefresh ? "pulse-dot" : ""} />
            Auto
          </button>

          <button onClick={fetchSnapshot} className="btn-accent" style={{ padding: "4px 12px", fontSize: "0.75rem" }}>
            <RefreshCw size={12} style={{ display: "inline", marginRight: 4, verticalAlign: "middle" }} />
            Refresh
          </button>
        </div>
      </div>
      </StaggerItem>

      {/* Error */}
      {error && (
        <div className="fade-in" style={{
          padding: "10px 16px", borderRadius: 8, marginBottom: 16,
          background: "rgba(255,71,87,0.08)", border: "1px solid rgba(255,71,87,0.2)",
          color: "var(--danger)", fontSize: "0.8rem",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      {/* Loading */}
      {loading && !snapshot && (
        <div style={{ textAlign: "center", padding: 80 }}>
          <div className="pulse-dot" style={{
            width: 48, height: 48, borderRadius: "50%",
            background: "var(--accent-dim)", border: "2px solid var(--accent)",
            margin: "0 auto 16px",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Eye size={20} color="var(--accent)" />
          </div>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Loading OI Intelligence...</div>
        </div>
      )}

      {/* Main Content */}
      {snapshot && (
        <StaggerItem>
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <PCRGauge pcr={snapshot.pcr} trend={snapshot.pcr_trend} confidence={snapshot.confidence} />
            <OverallBiasCard snapshot={snapshot} />
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <PCRSparkline history={snapshot.pcr_history} />
              <BiasTimeline history={snapshot.bias_history} />
            </div>
          </div>

          {/* Market State Engine */}
          <div>
            <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600, marginBottom: 10, display: "flex", alignItems: "center", gap: 6 }}>
              <Activity size={14} /> MARKET STATE
            </div>
            <MarketStatePanel marketState={snapshot.market_state} />
          </div>

          <div>
            <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600, marginBottom: 10, display: "flex", alignItems: "center", gap: 6 }}>
              <Shield size={14} /> UNDERLYING ANALYSIS
            </div>
            <UnderlyingSummaryCards summaries={snapshot.underlying_summaries} />
          </div>

          <StrikeHeatmap entries={snapshot.strike_heatmap} />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ShortCoveringPanel signals={snapshot.short_covering_signals} />
            <ExecutionQualityPanel
              quality={snapshot.execution_quality}
              scSignals={snapshot.short_covering_signals}
            />
          </div>
        </div>
        </StaggerItem>
      )}
    </StaggerContainer>
  );
}
