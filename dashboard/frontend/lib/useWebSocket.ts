"use client";
/**
 * lib/useWebSocket.ts
 * Auto-reconnecting WebSocket hook that streams engine snapshots.
 *
 * PRODUCTION (Vercel): WebSocket MUST go to Railway — Vercel does not support WS.
 * Set NEXT_PUBLIC_WS_URL or NEXT_PUBLIC_BACKEND_URL so we never use same-domain /ws.
 *
 * REST polling fallback: if WS fails 3+ times, switches to /api/snapshot
 * polling every 5s — pauses when the tab is hidden.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import type { EngineSnapshot } from "./api";

const POLL_INTERVAL_MS = 5_000;
const MAX_WS_RETRIES_BEFORE_POLLING = 3;
const WS_BACKOFF_BASE_MS = 3000;
const WS_BACKOFF_MAX_MS = 30000;

/** Derive WS URL from env or return "" (no WS connection attempted). */
function getWsUrl(): string {
  const env = process.env.NEXT_PUBLIC_WS_URL;
  if (env && env.trim()) return env.trim();
  const backend = (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim();
  if (backend) {
    const wsProto = backend.startsWith("https") ? "wss:" : "ws:";
    const host = backend.replace(/^https?:\/\//, "").replace(/\/$/, "");
    return `${wsProto}//${host}/ws`;
  }
  if (typeof window !== "undefined") {
    const hostname = window.location.hostname;
    if (hostname === "localhost" || hostname === "127.0.0.1")
      return `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  }
  return "";
}

function getBackendBase(): string {
  return (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim().replace(/\/$/, "");
}

const BASE = getBackendBase();

export type WsStatus = "connecting" | "connected" | "disconnected" | "polling";
export type DataSource = "live" | "delayed" | "disconnected";

export function useEngineSocket() {
  const [snapshot,          setSnapshot         ] = useState<EngineSnapshot | null>(null);
  const [status,            setStatus           ] = useState<WsStatus>("disconnected");
  const [lastPing,          setLastPing         ] = useState<number>(0);
  const [snapshotReceivedAt, setSnapshotReceivedAt] = useState<number>(0);
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
      // Pause fetching when tab is hidden to save API quota
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      try {
        const r = await fetch(`${BASE}/api/snapshot`, { cache: "no-store" });
        if (r.ok) {
          const data = await r.json();
          setSnapshot(data as EngineSnapshot);
        }
      } catch { /* network error — keep polling */ }
    };

    poll(); // immediate first fetch
    pollRef.current = setInterval(poll, POLL_INTERVAL_MS);
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

    if (failCount.current >= MAX_WS_RETRIES_BEFORE_POLLING) {
      startPolling();
      return;
    }

    const wsUrl = getWsUrl();
    if (!wsUrl) {
      if (typeof console !== "undefined") console.warn("WS CONNECTING → (no URL; Vercel needs NEXT_PUBLIC_WS_URL or NEXT_PUBLIC_BACKEND_URL)");
      failCount.current += 1;
      startPolling();
      return;
    }

    if (typeof console !== "undefined") console.log("WS CONNECTING →", wsUrl);
    setStatus("connecting");
    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (typeof console !== "undefined") console.log("WS CONNECTED");
        setStatus("connected");
        failCount.current = 0;
        stopPolling();
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "snapshot" && msg.data) {
            setSnapshot(msg.data as EngineSnapshot);
            setSnapshotReceivedAt(Date.now());
          }
          if (msg.type === "ltp" && msg.data && typeof msg.data === "object") {
            setSnapshot((prev) =>
              prev ? { ...prev, index_ltp: msg.data as Record<string, number> } : prev
            );
            setSnapshotReceivedAt(Date.now());
          }
          if (msg.type === "ping" || msg.type === "keepalive") setLastPing(Date.now());
        } catch { /* ignore parse errors */ }
      };

      ws.onerror = () => {
        if (typeof console !== "undefined") console.warn("WS FAILED (error)");
        ws.close();
      };

      ws.onclose = () => {
        if (typeof console !== "undefined") console.warn("WS FAILED (close)");
        setStatus("disconnected");
        failCount.current += 1;
        // If the snapshot is very stale (> 30s), clear it so the UI shows "—" rather
        // than confidently displaying old data as if it were current.
        setSnapshotReceivedAt((prev) => {
          if (prev > 0 && Date.now() - prev > 30_000) {
            setSnapshot(null);
            return 0;
          }
          return prev;
        });
        if (!deadRef.current) {
          const delay = Math.min(
            WS_BACKOFF_BASE_MS * Math.pow(2, failCount.current - 1),
            WS_BACKOFF_MAX_MS
          );
          retryRef.current = setTimeout(connect, delay);
        }
      };
    } catch (err) {
      if (typeof console !== "undefined") console.warn("WS FAILED (throw)", err);
      failCount.current += 1;
      if (!deadRef.current) {
        const delay = Math.min(
          WS_BACKOFF_BASE_MS * Math.pow(2, failCount.current - 1),
          WS_BACKOFF_MAX_MS
        );
        retryRef.current = setTimeout(connect, delay);
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

  // Reconnect when the tab becomes visible again (handles mobile sleep / network drops).
  // Also recovers from polling-only mode — resets failCount so WS is tried before REST.
  useEffect(() => {
    const handleVisibility = () => {
      if (typeof document === "undefined" || document.visibilityState !== "visible") return;
      const wsAlive = wsRef.current?.readyState === WebSocket.OPEN;
      if (wsAlive) return; // already connected, nothing to do
      if (typeof console !== "undefined") console.log("WS — tab visible, attempting reconnect");
      if (retryRef.current) clearTimeout(retryRef.current);
      failCount.current = 0; // reset so WS is tried before falling back to polling
      if (pollingRef.current) stopPolling(); // drop polling — WS takes priority
      connect();
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [connect, stopPolling]);

  return { snapshot, status, lastPing, snapshotReceivedAt };
}
