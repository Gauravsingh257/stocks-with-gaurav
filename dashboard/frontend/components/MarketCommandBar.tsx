"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import Sparkline from "@/components/Sparkline";
import { API_BASE } from "@/lib/api";

const MAX_HISTORY = 40;
const LABELS = ["NIFTY 50", "NIFTY BANK"] as const;
const SHORT_KEYS = { "NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY" } as const;

interface HealthData {
  engine_status?: string;
  engine_live?: boolean;
  kite_connected?: boolean;
  token_present?: boolean;
  ws_clients?: number;
  backend_version?: string;
}

type DataSource = "live" | "delayed" | "disconnected";

interface TickData {
  price: number;
  change: number;
  percentChange: number;
}

/** Market session from current time in IST (NSE: 9:15–15:30, preopen 9:00–9:15). */
function getMarketSession(): "OPEN" | "PREOPEN" | "CLOSED" {
  const now = new Date();
  const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const hour = ist.getHours();
  const minute = ist.getMinutes();
  const minutes = hour * 60 + minute;
  const open = 9 * 60 + 15;
  const preopen = 9 * 60;
  const close = 15 * 60 + 30;
  if (minutes >= open && minutes <= close) return "OPEN";
  if (minutes >= preopen && minutes < open) return "PREOPEN";
  return "CLOSED";
}

/** Full number only (no "k" shorthand). e.g. 23409, 54413 */
function formatLtp(v: number): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  const n = v >= 1 ? Math.round(v) : v;
  return n >= 1 ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : v.toFixed(2);
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
  const [health, setHealth] = useState<HealthData | null>(null);
  const [history, setHistory] = useState<{ NIFTY: number[]; BANKNIFTY: number[] }>({
    NIFTY: [],
    BANKNIFTY: [],
  });
  const [flashClass, setFlashClass] = useState<{ NIFTY: string; BANKNIFTY: string }>({
    NIFTY: "",
    BANKNIFTY: "",
  });
  const [backendTimestamp, setBackendTimestamp] = useState<string | null>(null);
  const [apiNifty, setApiNifty] = useState<number | null>(null);
  const [apiBanknifty, setApiBanknifty] = useState<number | null>(null);
  const [signalCount, setSignalCount] = useState<number>(0);
  // Increments every second to drive "X sec ago" recomputation
  const [tick, setTick] = useState(0);
  const prevPriceRef = useRef<Record<string, number>>({});

  // Health fetch for engine, kite, version only (no market status)
  useEffect(() => {
    const fetchHealth = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      const base = API_BASE || "";
      fetch(`${base}/api/system/health`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setHealth(d))
        .catch(() => {});
    };
    fetchHealth();
    const t = setInterval(fetchHealth, 30_000);
    return () => clearInterval(t);
  }, []);

  // Poll web backend for signal count (snapshot is primary for engine status)
  useEffect(() => {
    const base = API_BASE || "";
    const fetchSnap = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      fetch(`${base}/api/snapshot`)
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

  // 1-second ticker — drives "X sec ago" label recomputation
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1_000);
    return () => clearInterval(t);
  }, []);

  // Tick-based history and flash: every WebSocket snapshot with index_ltp
  useEffect(() => {
    const indexLtp = snapshot?.index_ltp;
    if (!indexLtp || typeof indexLtp !== "object") return;

    const updates: { key: "NIFTY" | "BANKNIFTY"; price: number }[] = [];
    if (typeof indexLtp["NIFTY 50"] === "number") {
      updates.push({ key: "NIFTY", price: indexLtp["NIFTY 50"] });
    }
    if (typeof indexLtp["NIFTY BANK"] === "number") {
      updates.push({ key: "BANKNIFTY", price: indexLtp["NIFTY BANK"] });
    }

    if (updates.length === 0) return;

    setHistory((prev) => {
      const next = { ...prev };
      for (const { key, price } of updates) {
        next[key] = pushTick(prev[key], price, MAX_HISTORY);
      }
      return next;
    });

    // Flash class: compare with previous price
    const nextFlash: { NIFTY: string; BANKNIFTY: string } = { NIFTY: "", BANKNIFTY: "" };
    let scheduleClear = false;
    for (const { key, price } of updates) {
      const label = key === "NIFTY" ? "NIFTY 50" : "NIFTY BANK";
      const prev = prevPriceRef.current[label];
      if (prev !== undefined && prev !== null) {
        if (price > prev) nextFlash[key] = "price-up";
        else if (price < prev) nextFlash[key] = "price-down";
        scheduleClear = true;
      }
      prevPriceRef.current[label] = price;
    }
    setFlashClass((prev) => ({ ...prev, ...nextFlash }));
    if (scheduleClear) {
      const t = setTimeout(() => setFlashClass({ NIFTY: "", BANKNIFTY: "" }), 600);
      return () => clearTimeout(t);
    }
  }, [snapshot?.index_ltp, snapshot?.snapshot_time]);

  // Ticks derived from snapshot (for change/percent)
  const ticks = useMemo(() => {
    const indexLtp = snapshot?.index_ltp;
    if (!indexLtp || typeof indexLtp !== "object") return {} as Record<string, TickData>;
    const out: Record<string, TickData> = {};
    for (const label of LABELS) {
      const price = indexLtp[label];
      if (typeof price !== "number") continue;
      const prev = prevPriceRef.current[label];
      const change = prev !== undefined ? price - prev : 0;
      const percentChange = prev !== undefined && prev !== 0 ? (change / prev) * 100 : 0;
      out[label] = { price, change, percentChange };
    }
    return out;
  }, [snapshot?.index_ltp, snapshot?.snapshot_time]);

  // ── Derived state ──────────────────────────────────────────────────────────

  // Snapshot age in seconds (recomputes every tick via `tick` dependency)
  const snapshotAgeSeconds = useMemo(() => {
    void tick; // force recompute every second
    // Use the WS-received timestamp if available (most accurate), else fall back to
    // the snapshot_time field from REST polling
    if (snapshotReceivedAt > 0) return Math.floor((Date.now() - snapshotReceivedAt) / 1_000);
    const t = snapshot?.snapshot_time ?? backendTimestamp;
    if (!t) return null;
    return Math.floor((Date.now() - new Date(t).getTime()) / 1_000);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, snapshotReceivedAt, snapshot?.snapshot_time, backendTimestamp]);

  // Human-readable "X sec ago" label
  const ageLabel = useMemo(() => {
    if (snapshotAgeSeconds === null) return null;
    if (snapshotAgeSeconds < 5)    return "just now";
    if (snapshotAgeSeconds < 60)   return `${snapshotAgeSeconds}s ago`;
    if (snapshotAgeSeconds < 3600) return `${Math.floor(snapshotAgeSeconds / 60)}m ago`;
    return `${Math.floor(snapshotAgeSeconds / 3600)}h ago`;
  }, [snapshotAgeSeconds]);

  // Data source badge: LIVE / DELAYED / DISCONNECTED
  const dataSource = useMemo<DataSource>(() => {
    // Redis unavailable flag from snapshot (backend populates this)
    if ((snapshot as unknown as Record<string, unknown>)?.redis_available === false &&
        (snapshot as unknown as Record<string, unknown>)?.data_source === "memory_cache")
      return "delayed";
    if (status === "connected" && snapshotAgeSeconds !== null && snapshotAgeSeconds < 15) return "live";
    if (status === "polling"   && snapshotAgeSeconds !== null && snapshotAgeSeconds < 60) return "delayed";
    if (snapshotAgeSeconds !== null && snapshotAgeSeconds < 60) return "delayed";
    if (!snapshot) return "disconnected";
    return "disconnected";
  }, [status, snapshotAgeSeconds, snapshot]);

  const dataSourceConfig = {
    live:         { emoji: "🟢", label: "LIVE",         color: "text-green-400",  title: "WebSocket — real-time data" },
    delayed:      { emoji: "🟡", label: "DELAYED",      color: "text-yellow-400", title: "REST polling or Redis cache — data may be a few seconds old" },
    disconnected: { emoji: "🔴", label: "DISCONNECTED", color: "text-red-400",    title: "No data source connected" },
  } as const;

  // Kite / token badge
  const kiteConfig = useMemo(() => {
    if (health === null) return { text: "Kite …", color: "text-slate-400", title: undefined };
    if (health.kite_connected === true)
      return { text: "Kite ON",       color: "text-green-400",  title: "Kite API connected" };
    if (health.token_present === false)
      return { text: "🔐 Login",      color: "text-yellow-400", title: "Run RUN_ENGINE_ON_RAILWAY.bat to generate a new Zerodha token" };
    if (health.token_present === true && health.kite_connected === false) {
      const ttl = (health as Record<string, unknown>).token_expires_in_hours as number | undefined;
      if (ttl != null && ttl > 20)
        return { text: "Kite checking…", color: "text-yellow-400", title: "Token present, verifying session — may take a minute" };
      return { text: "Kite expired",  color: "text-orange-400", title: "Token invalid or expired — run login bat" };
    }
    return { text: "Kite OFF", color: "text-gray-400", title: undefined };
  }, [health]);

  const session = getMarketSession();
  const marketStatusColor =
    session === "OPEN" ? "text-green-400" : session === "PREOPEN" ? "text-yellow-400" : "text-gray-400";

  const hasSnapshot = snapshot != null;
  const engineOn = hasSnapshot ? Boolean(snapshot.engine_running ?? snapshot.engine_live) : null;
  const sigToday = snapshot?.signals_today ?? signalCount;
  const maxSig = snapshot?.max_daily_signals ?? 5;
  const ds = dataSourceConfig[dataSource];

  return (
    <div
      className="w-full bg-black/50 backdrop-blur-md border-b border-cyan-500/20 px-3 py-1.5 md:px-4 md:py-2 flex flex-wrap gap-3 md:gap-6 text-xs md:text-sm items-center shrink-0 shadow-[0_0_10px_rgba(0,255,255,0.08)]"
      role="status"
      aria-label="Market command bar"
    >
      <span className="text-gray-400 font-medium uppercase tracking-wider">Indices</span>

      {LABELS.map((label) => {
        const tickData = ticks[label];
        const short = SHORT_KEYS[label];
        const priceOverride =
          label === "NIFTY 50"
            ? apiNifty ?? tickData?.price
            : label === "NIFTY BANK"
            ? apiBanknifty ?? tickData?.price
            : tickData?.price;
        const price = priceOverride ?? null;
        const change = tickData?.change ?? 0;
        const percentChange = tickData?.percentChange ?? 0;
        const changeColor =
          change > 0 ? "text-green-400" : change < 0 ? "text-red-400" : "text-gray-400";
        const deltaText =
          change > 0 ? `+${change.toFixed(0)}` : change < 0 ? `${change.toFixed(0)}` : "0";
        const deltaColor =
          change > 0 ? "text-green-400" : change < 0 ? "text-red-400" : "text-gray-400";
        const historyData = history[short];
        const flash = flashClass[short] || "";

        return (
          <span key={label} className="inline-flex items-center">
            <span className="font-mono font-semibold text-slate-200">
              {short}{" "}
              <span className={`${changeColor} ${flash}`}>
                {price != null ? formatLtp(price) : "—"}
                {price != null && (
                  <>
                    {" "}
                    <span aria-hidden>{change >= 0 ? "▲" : "▼"}</span>
                    <span className="ml-1 text-xs">{formatPercent(percentChange)}</span>
                    <span className={`ml-2 text-xs font-semibold ${deltaColor}`}>
                      {deltaText}
                    </span>
                  </>
                )}
              </span>
            </span>
            {historyData.length >= 2 && (
              <Sparkline
                data={historyData}
                positive={change >= 0}
                className="hidden sm:inline-block"
              />
            )}
          </span>
        );
      })}

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className={`font-semibold uppercase tracking-wide ${marketStatusColor}`}>
        {session === "PREOPEN" ? "PREMARKET" : session === "OPEN" ? "OPEN" : "CLOSED"}
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className={engineOn === true ? "text-green-400" : engineOn === false ? "text-red-400" : "text-slate-400"}>
        Engine {engineOn === true ? "ON" : engineOn === false ? "OFF" : "…"}
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className="text-cyan-400 font-medium">
        Signals <span className="text-slate-200">{sigToday}/{maxSig}</span>
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      {/* Data source badge: LIVE / DELAYED / DISCONNECTED */}
      <span className={`flex items-center gap-1 font-semibold ${ds.color}`} title={ds.title}>
        <span aria-hidden>{ds.emoji}</span>
        <span className="uppercase tracking-wide text-xs">{ds.label}</span>
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      {/* Kite / token badge */}
      <span className={kiteConfig.color} title={kiteConfig.title}>
        {kiteConfig.text}
      </span>

      <span className="text-slate-600 ml-auto flex items-center gap-3">
        {/* "Last updated: X sec ago" */}
        {ageLabel && (
          <span
            className={`font-mono text-xs ${snapshotAgeSeconds !== null && snapshotAgeSeconds > 30 ? "text-yellow-500" : "text-slate-500"}`}
            title="Time since last data update"
          >
            {ageLabel}
          </span>
        )}
        {health?.backend_version && (
          <span className="text-slate-600">v{health.backend_version}</span>
        )}
      </span>
    </div>
  );
}
