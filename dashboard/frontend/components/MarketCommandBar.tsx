"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import Sparkline from "@/components/Sparkline";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

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

function formatLtp(v: number): string {
  return v >= 1000 ? (v / 1000).toFixed(2) + "k" : v.toFixed(2);
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
  const { snapshot, status } = useEngineSocket();
  const [health, setHealth] = useState<HealthData | null>(null);
  const [history, setHistory] = useState<{ NIFTY: number[]; BANKNIFTY: number[] }>({
    NIFTY: [],
    BANKNIFTY: [],
  });
  const [flashClass, setFlashClass] = useState<{ NIFTY: string; BANKNIFTY: string }>({
    NIFTY: "",
    BANKNIFTY: "",
  });
  const prevPriceRef = useRef<Record<string, number>>({});

  // Health fetch for engine, kite, version only (no market status)
  useEffect(() => {
    const fetchHealth = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      fetch(`${BASE}/api/system/health`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setHealth(d))
        .catch(() => {});
    };
    fetchHealth();
    const t = setInterval(fetchHealth, 30_000);
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

  const session = getMarketSession();
  const marketStatusColor =
    session === "OPEN" ? "text-green-400" : session === "PREOPEN" ? "text-yellow-400" : "text-gray-400";

  const engineStatus = health?.engine_status ?? "offline";
  const engineStale = engineStatus === "stale" || engineStatus === "offline";
  const sigToday = snapshot?.signals_today ?? 0;
  const maxSig = snapshot?.max_daily_signals ?? 5;
  const kiteOk = health?.kite_connected === true;

  const wsStateLabel =
    status === "connected" ? "WS" : status === "polling" ? "REST" : "OFF";
  const wsStateColor =
    status === "connected" ? "text-green-400" : status === "polling" ? "text-yellow-400" : "text-red-400";

  return (
    <div
      className="w-full bg-black/50 backdrop-blur-md border-b border-cyan-500/20 px-3 py-1.5 md:px-4 md:py-2 flex flex-wrap gap-3 md:gap-6 text-xs md:text-sm items-center shrink-0 shadow-[0_0_10px_rgba(0,255,255,0.08)]"
      role="status"
      aria-label="Market command bar"
    >
      <span className="text-gray-400 font-medium uppercase tracking-wider">Indices</span>

      {LABELS.map((label) => {
        const tick = ticks[label];
        const short = SHORT_KEYS[label];
        const price = tick?.price;
        const change = tick?.change ?? 0;
        const percentChange = tick?.percentChange ?? 0;
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
      <span className={engineStale ? "text-red-400" : "text-green-400"}>
        Engine {engineStatus === "running" ? "LIVE" : engineStatus === "stale" ? "STALE" : "OFF"}
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className="text-cyan-400 font-medium">
        Signals <span className="text-slate-200">{sigToday}/{maxSig}</span>
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className={`flex items-center gap-1 ${wsStateColor}`}>
        {status === "connected" ? (
          <span title="WebSocket connected">🟢</span>
        ) : status === "polling" ? (
          <span title="REST fallback">🟡</span>
        ) : (
          <span title="Disconnected">🔴</span>
        )}
        <span>{wsStateLabel}</span>
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className={kiteOk ? "text-green-400" : "text-gray-400"}>
        Kite {kiteOk ? "ON" : "OFF"}
      </span>

      {health?.backend_version && (
        <span className="text-slate-600 ml-auto">v{health.backend_version}</span>
      )}
    </div>
  );
}
