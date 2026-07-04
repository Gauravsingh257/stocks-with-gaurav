"use client";

/**
 * MarketCommandBar — institutional macro strip across the top of every page.
 *
 * Phase B redesign (2026-05-25):
 * - Show a Bloomberg-style horizontal strip of live indices: NIFTY 50,
 *   BANKNIFTY, GIFT NIFTY, INDIA VIX, USD/INR, GOLD, CRUDE OIL — driven
 *   by whichever symbols the engine snapshot's `index_ltp` dict
 *   actually contains. Symbols the user's Kite account can't see (e.g.
 *   no MCX/CDS segment access) are silently absent.
 * - All status fields (CLOSED / Engine ON / Signals / STANDBY / Kite ON)
 *   are collapsed into ONE small colored status dot at the far right.
 *   Hover reveals the full breakdown. Engine-health visibility is
 *   preserved without cluttering the bar.
 * - Sparklines removed — they wasted horizontal space and competed for
 *   attention with the price + percent.
 *
 * Wiring preserved (intentionally untouched):
 * - useEngineSocket() WebSocket subscription
 * - useMergedIndexLtp() merge of WS + snapshot LTP
 * - useHealth() backend health polling
 * - /api/snapshot polling for signal_count
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useMergedIndexLtp } from "@/lib/realtimeRegistry";
import { useHealth } from "@/lib/useHealth";
import { API_BASE } from "@/lib/api";
import { getMarketSession } from "@/lib/marketSession";

const MAX_HISTORY = 40;

/**
 * Indices rendered in order. `label` matches the key the engine writes
 * into snapshot.index_ltp (see smc_mtf_engine_v4.py::_publish_redis_snapshot
 * candle_to_label dict). `short` is what we render in the strip. A
 * symbol missing from snapshot.index_ltp at render time simply does
 * not appear — keeps the bar honest when Kite hasn't subscribed yet.
 */
const INDICES: ReadonlyArray<{ label: string; short: string; decimals: number }> = [
  { label: "NIFTY 50",   short: "NIFTY",      decimals: 0 },
  { label: "NIFTY BANK", short: "BANKNIFTY",  decimals: 0 },
  { label: "GIFT NIFTY", short: "GIFT NIFTY", decimals: 0 },
  { label: "INDIA VIX",  short: "INDIA VIX",  decimals: 2 },
  { label: "USDINR",     short: "USD/INR",    decimals: 2 },
  { label: "GOLD",       short: "GOLD",       decimals: 0 },
  { label: "CRUDE OIL",  short: "CRUDE",      decimals: 2 },
];

interface TickData {
  price: number;
  change: number;
  percentChange: number;
}

function formatLtp(v: number, decimals: number): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  if (decimals === 0) {
    return Math.round(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  return v.toFixed(decimals);
}

function formatPercent(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function pushTick(arr: number[], value: number, max: number): number[] {
  const next = [...arr, value];
  if (next.length > max) return next.slice(1);
  return next;
}

export default function MarketCommandBar() {
  const { snapshot, status, snapshotReceivedAt } = useEngineSocket();
  const indexLtpMerged = useMergedIndexLtp(snapshot?.index_ltp);
  const health = useHealth();

  // Per-symbol history + flash class — dynamic over INDICES instead of
  // hardcoded NIFTY/BANKNIFTY (the old shape).
  const [history, setHistory] = useState<Record<string, number[]>>({});
  const [flashClass, setFlashClass] = useState<Record<string, string>>({});
  const [backendTimestamp, setBackendTimestamp] = useState<string | null>(null);
  const [signalCount, setSignalCount] = useState<number>(0);
  const [tick, setTick] = useState(0);
  const prevPriceRef = useRef<Record<string, number>>({});

  // Snapshot poll (signal count + backend timestamp). Same cadence as
  // before (10s) — already exempted from the rate limiter in PR #10.
  useEffect(() => {
    const base = API_BASE || "";
    const fetchSnap = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      fetch(`${base}/api/snapshot`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (d && typeof d.signals_today === "number") setSignalCount(d.signals_today);
          if (d && d.snapshot_time) setBackendTimestamp(d.snapshot_time);
        })
        .catch(() => {});
    };
    fetchSnap();
    const t = setInterval(fetchSnap, 10_000);
    return () => clearInterval(t);
  }, []);

  // 1s ticker drives "X sec ago" recomputation.
  useEffect(() => {
    const t = setInterval(() => setTick(Date.now()), 1_000);
    return () => clearInterval(t);
  }, []);

  // Tick-based history + flash class. Iterates the full INDICES list
  // — whichever symbols have data this tick get updated; others are
  // left alone (so a temporary tick gap doesn't blank the strip).
  useEffect(() => {
    const indexLtp = indexLtpMerged;
    if (!indexLtp || typeof indexLtp !== "object") return;

    const priceMap: Record<string, number> = {};
    for (const { label, short } of INDICES) {
      const p = (indexLtp as Record<string, unknown>)[label];
      if (typeof p === "number" && Number.isFinite(p)) {
        priceMap[short] = p;
      }
    }
    if (Object.keys(priceMap).length === 0) return;

    setHistory((prev) => {
      const next = { ...prev };
      for (const [short, price] of Object.entries(priceMap)) {
        next[short] = pushTick(prev[short] ?? [], price, MAX_HISTORY);
      }
      return next;
    });

    const nextFlash: Record<string, string> = {};
    let scheduleClear = false;
    for (const [short, price] of Object.entries(priceMap)) {
      const prev = prevPriceRef.current[short];
      if (prev !== undefined) {
        if (price > prev) nextFlash[short] = "price-up";
        else if (price < prev) nextFlash[short] = "price-down";
        scheduleClear = true;
      }
      prevPriceRef.current[short] = price;
    }
    if (Object.keys(nextFlash).length > 0) {
      setFlashClass((prev) => ({ ...prev, ...nextFlash }));
    }
    if (scheduleClear) {
      const t = setTimeout(() => setFlashClass({}), 600);
      return () => clearTimeout(t);
    }
  }, [indexLtpMerged]);

  // Per-symbol tick data (price + change + percentChange)
  const ticks = useMemo(() => {
    const indexLtp = indexLtpMerged;
    if (!indexLtp || typeof indexLtp !== "object") return {} as Record<string, TickData>;
    const out: Record<string, TickData> = {};
    for (const { label, short } of INDICES) {
      const price = (indexLtp as Record<string, unknown>)[label];
      if (typeof price !== "number") continue;
      const series = history[short] ?? [];
      const prev = series.length >= 2 ? series[series.length - 2] : undefined;
      const change = prev !== undefined ? price - prev : 0;
      const percentChange = prev !== undefined && prev !== 0 ? (change / prev) * 100 : 0;
      out[short] = { price, change, percentChange };
    }
    return out;
  }, [history, indexLtpMerged]);

  // Snapshot freshness — same logic as before
  const snapshotAgeSeconds = useMemo(() => {
    if (tick <= 0) return null;
    if (snapshotReceivedAt > 0) return Math.floor((tick - snapshotReceivedAt) / 1_000);
    const t = snapshot?.snapshot_time ?? backendTimestamp;
    if (!t) return null;
    return Math.floor((tick - new Date(t).getTime()) / 1_000);
  }, [tick, snapshotReceivedAt, snapshot?.snapshot_time, backendTimestamp]);

  const ageLabel = useMemo(() => {
    if (snapshotAgeSeconds === null) return null;
    if (snapshotAgeSeconds < 5)    return "just now";
    if (snapshotAgeSeconds < 60)   return `${snapshotAgeSeconds}s ago`;
    if (snapshotAgeSeconds < 3600) return `${Math.floor(snapshotAgeSeconds / 60)}m ago`;
    return `${Math.floor(snapshotAgeSeconds / 3600)}h ago`;
  }, [snapshotAgeSeconds]);

  // ── Consolidated status dot ──────────────────────────────────────
  // Replaces the old 5-badge clutter (CLOSED / Engine ON / Signals /
  // STANDBY / Kite ON) with one colored dot. Hover reveals the full
  // breakdown. Color logic:
  //   GREEN  — engine running, kite connected, data fresh
  //   YELLOW — at least one of: engine flag missing, kite degraded,
  //            snapshot >60s old, market closed (everything else OK)
  //   RED    — engine off, kite off, OR no snapshot at all
  const session = getMarketSession();
  const hasSnapshot = snapshot != null;
  const engineOn = hasSnapshot ? Boolean(snapshot.engine_running ?? snapshot.engine_live) : null;
  const kiteOn = health?.kite_connected === true;
  const sigToday = snapshot?.signals_today ?? signalCount;
  const maxSig = snapshot?.max_daily_signals ?? 5;
  const dataFresh = snapshotAgeSeconds !== null && snapshotAgeSeconds < 15;

  type DotState = { color: string; bg: string; label: string };
  const dot: DotState = useMemo(() => {
    if (!hasSnapshot || engineOn === false || health?.token_present === false) {
      return { color: "bg-red-500", bg: "bg-red-500/10", label: "Engine offline or token missing" };
    }
    const isHealthy = engineOn === true && kiteOn && (dataFresh || session === "CLOSED");
    if (isHealthy) {
      return {
        color: "bg-green-400",
        bg: "bg-green-500/10",
        label: session === "CLOSED" ? "Healthy · market closed" : "Live · all systems normal",
      };
    }
    return { color: "bg-yellow-400", bg: "bg-yellow-500/10", label: "Degraded — see details" };
  }, [hasSnapshot, engineOn, kiteOn, dataFresh, session, health?.token_present]);

  // Tape regime + structure bias — moved here from the (removed) MarketIntelStrip
  // so the macro read stays visible without a second full-width panel.
  const regime = snapshot?.market_regime ?? "NEUTRAL";
  const niftyBias =
    snapshot?.setup_d_state?.["NIFTY"]?.bias ??
    snapshot?.setup_d_state?.["NIFTY 50"]?.bias ??
    regime;
  const biasCls = (v: string) => {
    const s = String(v).toUpperCase();
    return s.includes("BULL") ? "text-green-400" : s.includes("BEAR") ? "text-red-400" : "text-slate-300";
  };

  const statusTooltip = useMemo(() => {
    const sessionText =
      session === "OPEN" ? "OPEN" : session === "PREOPEN" ? "PREMARKET" : "CLOSED";
    const lines = [
      `Session: ${sessionText}`,
      `Engine: ${engineOn === true ? "running" : engineOn === false ? "stopped" : "unknown"}`,
      `Kite: ${kiteOn ? "connected" : health?.token_present === false ? "no token" : "disconnected"}`,
      `Signals today: ${sigToday}/${maxSig}`,
    ];
    if (ageLabel) lines.push(`Snapshot: ${ageLabel}`);
    return lines.join(" · ");
  }, [session, engineOn, kiteOn, health?.token_present, sigToday, maxSig, ageLabel]);

  return (
    <div
      className="w-full bg-black/55 backdrop-blur-md border-b border-cyan-500/15 px-3 py-1.5 md:px-4 md:py-2 flex items-center gap-4 md:gap-5 text-xs md:text-sm shrink-0 shadow-[0_0_10px_rgba(0,255,255,0.06)] overflow-x-auto"
      role="status"
      aria-label="Macro market strip"
    >
      {/* Tape regime + structure bias (compact) */}
      <span className="flex items-center gap-3 shrink-0 text-[0.62rem] uppercase tracking-wide">
        <span title="Tape regime — live classification">
          <span className="text-gray-500 mr-1">Tape</span>
          <span className={`font-semibold ${biasCls(String(regime))}`}>{String(regime)}</span>
        </span>
        <span title="Structure bias — engine snapshot">
          <span className="text-gray-500 mr-1">Bias</span>
          <span className={`font-semibold ${biasCls(String(niftyBias))}`}>{String(niftyBias)}</span>
        </span>
        <span className="text-slate-700 select-none" aria-hidden>|</span>
      </span>

      <span className="text-gray-500 font-medium uppercase tracking-[0.18em] text-[0.62rem] shrink-0">
        Indices
      </span>

      {INDICES.map(({ label, short, decimals }, idx) => {
        const t = ticks[short];
        const price = t?.price ?? null;
        if (price == null) {
          // Don't render placeholder cards for symbols Kite hasn't
          // subscribed to (cleaner than rendering 7 "—" cards).
          return null;
        }
        const change = t?.change ?? 0;
        const percentChange = t?.percentChange ?? 0;
        const flash = flashClass[short] || "";
        const dirColor =
          change > 0 ? "text-green-400" : change < 0 ? "text-red-400" : "text-slate-400";
        const arrow = change > 0 ? "▲" : change < 0 ? "▼" : "—";

        return (
          <div
            key={label}
            className="flex items-baseline gap-1.5 shrink-0 first:ml-0 ml-0"
            title={`${label} · ${formatLtp(price, decimals)}`}
          >
            <span className="text-gray-400 font-medium uppercase tracking-wide text-[0.68rem] mr-1">
              {short}
            </span>
            <span className={`font-mono font-semibold text-slate-100 ${flash}`}>
              {formatLtp(price, decimals)}
            </span>
            <span className={`text-[0.68rem] font-semibold tabular-nums ${dirColor}`}>
              <span aria-hidden className="mr-0.5">{arrow}</span>
              {formatPercent(percentChange)}
            </span>
            {idx < INDICES.length - 1 && (
              <span className="text-slate-700 ml-2 select-none" aria-hidden>
                ·
              </span>
            )}
          </div>
        );
      })}

      {/* Far-right: consolidated status dot + last-updated label */}
      <span className="ml-auto flex items-center gap-3 shrink-0">
        {ageLabel && (
          <span
            className={`font-mono text-[0.65rem] ${snapshotAgeSeconds !== null && snapshotAgeSeconds > 30 ? "text-yellow-500" : "text-slate-500"}`}
            title="Time since last data update"
          >
            {ageLabel}
          </span>
        )}
        <span
          className={`flex items-center justify-center w-2.5 h-2.5 rounded-full ${dot.color}`}
          title={`${dot.label}\n${statusTooltip}`}
          aria-label={dot.label}
        />
      </span>
    </div>
  );
}
