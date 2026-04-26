"use client";

import { useEngineSocket } from "@/lib/useWebSocket";
import { getMarketSession } from "@/lib/marketSession";

/**
 * One-line context when the live stream or engine snapshot is unavailable,
 * so the UI does not read as a hard failure (especially after hours).
 */
export default function BackendStatusNotice() {
  const { snapshot, status } = useEngineSocket();
  const session = getMarketSession();
  const hasFreshPath = status === "connected" || status === "polling";
  const engineHint = snapshot?.engine_running === true || snapshot?.engine_live === true;

  if (hasFreshPath && engineHint) return null;

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
