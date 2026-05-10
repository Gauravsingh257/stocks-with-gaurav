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
import { emitWatchlistOsHint } from "./watchlistOsBridge";
import { enqueueSequential } from "./stateEngine/eventQueue";
import {
  extractDataSnapshot,
  extractEnvelopeVersion,
  mergeDigestPatch,
  mergeSnapshot,
  nextStreamCursor,
  rejectOutOfOrderStream,
  rejectStaleSnapshotAge,
  rejectStaleUpdate,
} from "./stateEngine/reconcile";
import type { WsEnvelope } from "./stateEngine/types";
import { mergeIndexLtpOverlay, replaceIndexLtpFromSnapshot } from "./realtimeRegistry";

const POLL_INTERVAL_MS = 5_000;
const MAX_WS_RETRIES_BEFORE_POLLING = 3;
const WS_BACKOFF_BASE_MS = 3000;
const WS_BACKOFF_MAX_MS = 30000;

const wsDbg = (...args: unknown[]) => {
  if (process.env.NODE_ENV === "development") console.log(...args);
};

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
  const [globalStateVersion, setGlobalStateVersion] = useState(0);
  const [rejectedStaleUpdates, setRejectedStaleUpdates] = useState(0);
  const [rejectedOutOfOrder, setRejectedOutOfOrder] = useState(0);
  const [forcedResyncs, setForcedResyncs] = useState(0);
  const wsRef      = useRef<WebSocket | null>(null);
  const retryRef   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const deadRef    = useRef(false);
  const failCount  = useRef(0);
  const pollingRef = useRef(false);
  const lastGvRef  = useRef(0);
  const lastStreamSeqRef = useRef(0);

  const requestResync = useCallback(async () => {
    setForcedResyncs((x) => x + 1);
    try {
      const r = await fetch(`${BASE}/api/snapshot`, { cache: "no-store" });
      if (r.ok) {
        const data = (await r.json()) as EngineSnapshot;
        const gv = (data as unknown as { _global_state_version?: number })._global_state_version;
        if (typeof gv === "number") {
          lastGvRef.current = Math.max(lastGvRef.current, gv);
          setGlobalStateVersion(lastGvRef.current);
        }
        replaceIndexLtpFromSnapshot(data.index_ltp);
        setSnapshot(data);
        setSnapshotReceivedAt(Date.now());
      }
    } catch {
      /* keep prior snapshot */
    }
  }, []);

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
          const data = (await r.json()) as EngineSnapshot;
          const gv = (data as unknown as { _global_state_version?: number })._global_state_version;
          if (typeof gv === "number") {
            lastGvRef.current = Math.max(lastGvRef.current, gv);
            setGlobalStateVersion(lastGvRef.current);
          }
          replaceIndexLtpFromSnapshot(data.index_ltp);
          setSnapshot(data);
          setSnapshotReceivedAt(Date.now());
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
      wsDbg("WS CONNECTING → (no URL; Vercel needs NEXT_PUBLIC_WS_URL or NEXT_PUBLIC_BACKEND_URL)");
      failCount.current += 1;
      startPolling();
      return;
    }

    wsDbg("WS CONNECTING →", wsUrl);
    setStatus("connecting");
    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        wsDbg("WS CONNECTED");
        lastStreamSeqRef.current = 0;
        setStatus("connected");
        failCount.current = 0;
        stopPolling();
      };

      ws.onmessage = (ev) => {
        enqueueSequential(() => {
          let msg: WsEnvelope & Record<string, unknown>;
          try {
            msg = JSON.parse(ev.data as string) as WsEnvelope & Record<string, unknown>;
          } catch {
            return;
          }

          const seq =
            typeof msg.stream_sequence === "number" ? msg.stream_sequence : undefined;
          if (rejectOutOfOrderStream(lastStreamSeqRef.current, seq)) {
            setRejectedOutOfOrder((n) => n + 1);
            return;
          }
          lastStreamSeqRef.current = nextStreamCursor(lastStreamSeqRef.current, seq);

          const gv = extractEnvelopeVersion(msg);
          if (gv !== undefined) {
            if (rejectStaleUpdate(lastGvRef.current, gv)) {
              setRejectedStaleUpdates((n) => n + 1);
              void requestResync();
              return;
            }
            lastGvRef.current = Math.max(lastGvRef.current, gv);
            setGlobalStateVersion(lastGvRef.current);
          }

          const t = msg.type;
          if (t === "snapshot" && msg.data) {
            const snap = extractDataSnapshot(msg.data);
            if (snap) {
              const age = (snap as unknown as { _snapshot_age_ms?: number | null })._snapshot_age_ms;
              if (rejectStaleSnapshotAge(age ?? null)) {
                void requestResync();
                return;
              }
              const idx = (snap as { index_ltp?: Record<string, number> }).index_ltp;
              replaceIndexLtpFromSnapshot(idx);
              setSnapshot((prev) => mergeSnapshot(prev, snap));
              setSnapshotReceivedAt(Date.now());
            }
            return;
          }

          if (t === "digest" && msg.data && typeof msg.data === "object") {
            const raw = msg.data as Record<string, unknown>;
            const idx = raw.index_ltp;
            if (idx && typeof idx === "object") {
              mergeIndexLtpOverlay(idx as Record<string, number>);
            }
            setSnapshot((prev) => mergeDigestPatch(prev, raw));
            setSnapshotReceivedAt(Date.now());
            return;
          }

          if (t === "ltp" && msg.data && typeof msg.data === "object") {
            const patch = msg.data as Record<string, number>;
            mergeIndexLtpOverlay(patch);
            setSnapshot((prev) =>
              prev ? { ...prev, index_ltp: { ...(prev.index_ltp || {}), ...patch } } : prev
            );
            setSnapshotReceivedAt(Date.now());
            return;
          }

          if (t === "ping" || t === "keepalive") setLastPing(Date.now());

          if (t === "watchlist_delta") {
            const inner = msg.data && typeof msg.data === "object" ? (msg.data as { user_id?: number; kind?: string }) : {};
            const uid =
              typeof msg.user_id === "number"
                ? msg.user_id
                : typeof inner.user_id === "number"
                  ? inner.user_id
                  : undefined;
            const kind =
              typeof msg.kind === "string"
                ? msg.kind
                : typeof inner.kind === "string"
                  ? inner.kind
                  : "hint";
            if (typeof uid === "number") emitWatchlistOsHint(uid, kind);
          }
        });
      };

      ws.onerror = () => {
        wsDbg("WS FAILED (error)");
        ws.close();
      };

      ws.onclose = () => {
        wsDbg("WS closed — reconnecting with backoff");
        setStatus("disconnected");
        failCount.current += 1;
        // Keep last snapshot during reconnect — REST polling / digest merge still valid as LKG.
        if (!deadRef.current) {
          const delay = Math.min(
            WS_BACKOFF_BASE_MS * Math.pow(2, failCount.current - 1),
            WS_BACKOFF_MAX_MS
          );
          retryRef.current = setTimeout(connect, delay);
        }
      };
    } catch (err) {
      wsDbg("WS FAILED (throw)", err);
      failCount.current += 1;
      if (!deadRef.current) {
        const delay = Math.min(
          WS_BACKOFF_BASE_MS * Math.pow(2, failCount.current - 1),
          WS_BACKOFF_MAX_MS
        );
        retryRef.current = setTimeout(connect, delay);
      }
    }
  }, [startPolling, stopPolling, requestResync]);

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
      void requestResync();
      const wsAlive = wsRef.current?.readyState === WebSocket.OPEN;
      if (wsAlive) return;
      wsDbg("WS — tab visible, attempting reconnect");
      if (retryRef.current) clearTimeout(retryRef.current);
      failCount.current = 0; // reset so WS is tried before falling back to polling
      if (pollingRef.current) stopPolling(); // drop polling — WS takes priority
      connect();
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [connect, stopPolling, requestResync]);

  const snapshotAgeMs = snapshot?._snapshot_age_ms ?? null;
  const snapshotLikelyStale =
    snapshotAgeMs !== null &&
    snapshotAgeMs !== undefined &&
    rejectStaleSnapshotAge(snapshotAgeMs);

  return {
    snapshot,
    status,
    lastPing,
    snapshotReceivedAt,
    globalStateVersion,
    rejectedStaleUpdates,
    rejectedOutOfOrder,
    forcedResyncs,
    snapshotLikelyStale,
    requestResync,
  };
}
