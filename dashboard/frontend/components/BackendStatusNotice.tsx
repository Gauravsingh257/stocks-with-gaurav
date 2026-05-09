"use client";

import { useEngineSocket } from "@/lib/useWebSocket";
import { getMarketSession } from "@/lib/marketSession";

/**
 * Stream / snapshot context: prefer verified snapshot language over “degraded” jargon.
 * Suppresses banners during brief WS reconnect when a prior snapshot is still in memory.
 */
export default function BackendStatusNotice() {
  const { snapshot, status } = useEngineSocket();
  const session = getMarketSession();
  const hasFreshPath = status === "connected" || status === "polling";
  const engineHint = snapshot?.engine_running === true || snapshot?.engine_live === true;
  const staleData = snapshot?.stale === true;

  // Silent recovery: do not flash yellow bars while the socket cycle runs but data is still on screen
  if (snapshot && (status === "connecting" || status === "disconnected")) {
    return null;
  }

  if (hasFreshPath && engineHint && !staleData) return null;

  if (staleData && hasFreshPath && snapshot) {
    return (
      <div
        className="px-3 py-2 text-center text-xs md:text-sm shrink-0"
        style={{
          background: "rgba(245,158,11,0.12)",
          borderBottom: "1px solid rgba(245,158,11,0.28)",
          color: "var(--text-secondary)",
        }}
        role="status"
      >
        <strong style={{ color: "var(--warning)" }}>Realtime feed reconnecting</strong> — showing latest verified market snapshot
        {snapshot.snapshot_time ? ` · ${snapshot.snapshot_time}` : ""}
      </div>
    );
  }

  const closed = session === "CLOSED";
  const message = !hasFreshPath
    ? closed
      ? "Market is closed. Live engine stream may be idle; research data below still loads from the API when the backend is up."
      : "Connecting to the live data stream. Panels below use the REST API and may populate before the stream connects."
    : "Engine snapshot not active yet. Open research and scans still work when the Railway backend is reachable.";

  return (
    <div
      className="px-3 py-2 text-center text-xs md:text-sm shrink-0"
      style={{
        background: "rgba(245,158,11,0.08)",
        borderBottom: "1px solid rgba(245,158,11,0.2)",
        color: "var(--text-secondary)",
      }}
      role="status"
    >
      {message}
    </div>
  );
}
