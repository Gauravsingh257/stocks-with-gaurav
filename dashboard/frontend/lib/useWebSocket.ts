"use client";
/**
 * lib/useWebSocket.ts
 * Auto-reconnecting WebSocket hook that streams engine snapshots.
 * WebSocket URL auto-detects from page location so it works on both
 * localhost and Cloudflare tunnel (phone / remote access).
 *
 * Includes REST polling fallback: if WS fails 3+ times, switches to
 * /api/snapshot polling every 2 seconds, so the page always loads.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import type { EngineSnapshot } from "./api";

function getWsUrl(): string {
  const env = process.env.NEXT_PUBLIC_WS_URL;
  if (env) return env;
  if (typeof window === "undefined") return "ws://localhost:8000/ws";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

export type WsStatus = "connecting" | "connected" | "disconnected" | "polling";

export function useEngineSocket() {
  const [snapshot,  setSnapshot ] = useState<EngineSnapshot | null>(null);
  const [status,    setStatus   ] = useState<WsStatus>("disconnected");
  const [lastPing,  setLastPing ] = useState<number>(0);
  const wsRef      = useRef<WebSocket | null>(null);
  const retryRef   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const deadRef    = useRef(false);
  const failCount  = useRef(0);
  const pollingRef = useRef(false);

  /* ── REST polling fallback ──────────────────────────────────────────────── */
  const startPolling = useCallback(() => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    setStatus("polling");

    const poll = async () => {
      try {
        const r = await fetch(`${BASE}/api/snapshot`, { cache: "no-store" });
        if (r.ok) {
          const data = await r.json();
          setSnapshot(data as EngineSnapshot);
        }
      } catch { /* network error — keep polling */ }
    };

    poll(); // immediate first fetch
    pollRef.current = setInterval(poll, 2000);
  }, []);

  const stopPolling = useCallback(() => {
    pollingRef.current = false;
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  /* ── WebSocket connection ───────────────────────────────────────────────── */
  const connect = useCallback(() => {
    if (deadRef.current) return;

    // After 3 WS failures, switch to REST polling
    if (failCount.current >= 3) {
      startPolling();
      return;
    }

    setStatus("connecting");
    try {
      const ws = new WebSocket(getWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        failCount.current = 0;
        stopPolling(); // WS recovered, stop polling
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "snapshot" && msg.data) setSnapshot(msg.data as EngineSnapshot);
          if (msg.type === "ping" || msg.type === "keepalive") setLastPing(Date.now());
        } catch { /* ignore parse errors */ }
      };

      ws.onerror = () => ws.close();

      ws.onclose = () => {
        setStatus("disconnected");
        failCount.current += 1;
        if (!deadRef.current) {
          retryRef.current = setTimeout(connect, 3000);
        }
      };
    } catch {
      // WebSocket constructor can throw (e.g. bad URL)
      failCount.current += 1;
      if (!deadRef.current) {
        retryRef.current = setTimeout(connect, 3000);
      }
    }
  }, [startPolling, stopPolling]);

  useEffect(() => {
    deadRef.current = false;
    failCount.current = 0;
    connect();

    return () => {
      deadRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      stopPolling();
      wsRef.current?.close();
    };
  }, [connect, stopPolling]);

  return { snapshot, status, lastPing };
}
