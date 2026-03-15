"use client";

import { useEffect, useState } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { Wifi, WifiOff } from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

interface HealthData {
  market_status?: string;
  engine_status?: string;
  engine_live?: boolean;
  kite_connected?: boolean;
  token_present?: boolean;
  ws_clients?: number;
  backend_version?: string;
}

function formatLtp(v: number): string {
  return v >= 1000 ? (v / 1000).toFixed(2) + "k" : v.toFixed(2);
}

export default function MarketCommandBar() {
  const { snapshot, status } = useEngineSocket();
  const [health, setHealth] = useState<HealthData | null>(null);
  const [niftyLtp, setNiftyLtp] = useState<number | null>(null);
  const [bnfLtp, setBnfLtp] = useState<number | null>(null);

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

  // Optional: fetch last price from OHLC when Kite is connected (lightweight, 30s)
  useEffect(() => {
    if (!health?.kite_connected) return;
    const fetchLtp = async (symbol: string, setter: (v: number) => void) => {
      try {
        const r = await fetch(`${BASE}/api/ohlc/${encodeURIComponent(symbol)}?interval=5m&days=1`);
        if (!r.ok) return;
        const data = await r.json();
        const candles = data?.candles;
        if (Array.isArray(candles) && candles.length > 0) {
          const last = candles[candles.length - 1];
          if (typeof last?.close === "number") setter(last.close);
        }
      } catch {
        // ignore
      }
    };
    fetchLtp("NIFTY 50", setNiftyLtp);
    fetchLtp("NIFTY BANK", setBnfLtp);
    const id = setInterval(() => {
      fetchLtp("NIFTY 50", setNiftyLtp);
      fetchLtp("NIFTY BANK", setBnfLtp);
    }, 30_000);
    return () => clearInterval(id);
  }, [health?.kite_connected]);

  const marketStatus = health?.market_status ?? "closed";
  const isOpen = marketStatus === "open";
  const engineStatus = health?.engine_status ?? "offline";
  const engineStale = engineStatus === "stale" || engineStatus === "offline";
  const sigToday = snapshot?.signals_today ?? 0;
  const maxSig = snapshot?.max_daily_signals ?? 5;
  const wsConnected = status === "connected" || status === "polling";
  const kiteOk = health?.kite_connected === true;

  return (
    <div
      className="w-full bg-black/50 backdrop-blur-md border-b border-cyan-500/20 px-3 py-1.5 md:px-4 md:py-2 flex flex-wrap gap-3 md:gap-6 text-xs md:text-sm items-center shrink-0 shadow-[0_0_10px_rgba(0,255,255,0.08)]"
      role="status"
      aria-label="Market command bar"
    >
      <span className="text-gray-400 font-medium uppercase tracking-wider">Indices</span>
      <span className="font-mono font-semibold text-slate-200">
        NIFTY <span className="text-cyan-400">{niftyLtp != null ? formatLtp(niftyLtp) : "—"}</span>
      </span>
      <span className="text-slate-500">|</span>
      <span className="font-mono font-semibold text-slate-200">
        BANKNIFTY <span className="text-cyan-400">{bnfLtp != null ? formatLtp(bnfLtp) : "—"}</span>
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span
        className={`font-semibold uppercase tracking-wide ${isOpen ? "text-green-400" : "text-gray-400"}`}
      >
        {marketStatus === "premarket" ? "PREMARKET" : isOpen ? "OPEN" : "CLOSED"}
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
      <span className="flex items-center gap-1">
        {wsConnected ? (
          <Wifi size={12} className="text-green-400" />
        ) : (
          <WifiOff size={12} className="text-red-400" />
        )}
        <span className={wsConnected ? "text-green-400" : "text-red-400"}>
          {status === "connected" ? "WS" : status === "polling" ? "REST" : "OFF"}
        </span>
      </span>

      <span className="text-slate-500 hidden sm:inline">|</span>
      <span className={kiteOk ? "text-green-400" : "text-gray-400"}>
        Kite {kiteOk ? "ON" : "OFF"}
      </span>

      {health?.backend_version && (
        <>
          <span className="text-slate-600 ml-auto">v{health.backend_version}</span>
        </>
      )}
    </div>
  );
}
