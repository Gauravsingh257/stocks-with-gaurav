"use client";

/**
 * Phase 2 — useLiveTrades hook
 *
 * Consumes the new backend /api/trades + /ws/trades channel introduced for the
 * AI Trade Opportunity Terminal. Behaviour:
 *
 *   • On mount: fetches initial /api/trades and /api/discovery-feed
 *   • Opens /ws/trades for instant updates (signal / event / snapshot frames)
 *   • Falls back to 30s polling when WS is closed for >5s
 *   • Exposes simple { trades, events, status, error } state
 */

import { useEffect, useRef, useState } from "react";
import { getBackendBase } from "@/lib/api";

export interface LiveTradeIntelligence {
  probability: number;
  quality_score: number;
  risk_level: "LOW" | "MED" | "HIGH";
  expected_move_time: string;
  expected_outcome: string;
  components?: Record<string, number>;
}

export interface LiveTrade {
  id?: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  entry: number | null;
  sl: number | null;
  target: number | null;
  target2?: number | null;
  rr: number | null;
  setup: "A" | "B" | "C" | "D";
  confidence: "A+" | "A" | "B" | "C";
  status: "WAITING" | "APPROACHING" | "TAPPED" | "TRIGGERED" | "RUNNING" | "TARGET_HIT" | "STOP_HIT";
  score?: number | null;
  strategy?: string | null;
  timestamp?: string;
  // Phase 3 — intelligence fields (always present from /api/trades + /ws/trades)
  intelligence?: LiveTradeIntelligence;
  probability?: number;
  quality_score?: number;
  risk_level?: "LOW" | "MED" | "HIGH";
  expected_move_time?: string;
  expected_outcome?: string;
  narrative?: string;
  ranking_score?: number;
  analysis: {
    htf_bias: string;
    structure: string;
    liquidity: boolean;
    fvg: boolean;
    ob: boolean;
    reason: string;
    setup_grade?: string;
  };
}

export interface LiveEvent {
  type: string;
  symbol: string;
  time: string;
  ts: number;
  payload?: Record<string, unknown>;
}

export type LiveStatus = "connecting" | "live" | "polling" | "offline";

const POLL_INTERVAL_MS = 30_000;
const WS_RECONNECT_BASE_MS = 1_500;
const WS_RECONNECT_MAX_MS = 20_000;

function buildWsUrl(base: string): string {
  if (!base) return "";
  const url = new URL("/ws/trades", base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

interface UseLiveTradesResult {
  trades: LiveTrade[];
  events: LiveEvent[];
  status: LiveStatus;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useLiveTrades(apiKey?: string): UseLiveTradesResult {
  const [trades, setTrades] = useState<LiveTrade[]>([]);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttempts = useRef(0);
  const mountedRef = useRef(true);

  const base = getBackendBase();
  const headers: Record<string, string> = apiKey ? { "X-API-Key": apiKey } : {};
  const apiKeyQuery = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : "";

  const upsertTrade = (next: LiveTrade) => {
    setTrades((prev) => {
      const idx = prev.findIndex((t) => t.symbol === next.symbol);
      if (idx === -1) return [next, ...prev];
      const merged = [...prev];
      merged[idx] = { ...prev[idx], ...next };
      return merged;
    });
  };

  const prependEvent = (next: LiveEvent) => {
    setEvents((prev) => [next, ...prev].slice(0, 50));
  };

  const fetchInitial = async () => {
    try {
      const res = await fetch(`${base}/api/trades${apiKeyQuery}`, {
        headers,
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`/api/trades → ${res.status}`);
      const data = await res.json();
      setTrades(Array.isArray(data?.trades) ? data.trades : []);
      const feed = await fetch(`${base}/api/discovery-feed${apiKeyQuery}`, {
        headers,
        cache: "no-store",
      });
      if (feed.ok) {
        const fdata = await feed.json();
        setEvents(Array.isArray(fdata?.events) ? fdata.events : []);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "fetch failed");
    }
  };

  const startPolling = () => {
    if (pollRef.current) return;
    setStatus((s) => (s === "live" ? s : "polling"));
    pollRef.current = setInterval(fetchInitial, POLL_INTERVAL_MS);
  };

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const connectWs = () => {
    if (!base) {
      startPolling();
      setStatus("polling");
      return;
    }
    const url = buildWsUrl(base);
    const wsUrl = apiKey ? `${url}?api_key=${encodeURIComponent(apiKey)}` : url;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ws failed");
      startPolling();
      return;
    }
    wsRef.current = ws;
    setStatus("connecting");

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      setStatus("live");
      stopPolling();
    };

    ws.onmessage = (msg) => {
      try {
        const frame = JSON.parse(msg.data);
        if (frame.type === "snapshot" && frame.data) {
          if (Array.isArray(frame.data.trades)) setTrades(frame.data.trades);
          if (Array.isArray(frame.data.events)) setEvents(frame.data.events);
        } else if (frame.type === "signal" && frame.data) {
          upsertTrade(frame.data as LiveTrade);
        } else if (frame.type === "event" && frame.data) {
          prependEvent(frame.data as LiveEvent);
        } else if (frame.type === "ping") {
          ws.send(JSON.stringify({ type: "pong" }));
        }
      } catch {
        /* ignore malformed frame */
      }
    };

    ws.onerror = () => {
      setError("websocket error");
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (!mountedRef.current) return;
      setStatus("polling");
      startPolling();
      const delay = Math.min(
        WS_RECONNECT_MAX_MS,
        WS_RECONNECT_BASE_MS * 2 ** reconnectAttempts.current,
      );
      reconnectAttempts.current += 1;
      setTimeout(() => {
        if (mountedRef.current) connectWs();
      }, delay);
    };
  };

  useEffect(() => {
    mountedRef.current = true;
    fetchInitial();
    connectWs();
    return () => {
      mountedRef.current = false;
      stopPolling();
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, apiKey]);

  return { trades, events, status, error, refresh: fetchInitial };
}
