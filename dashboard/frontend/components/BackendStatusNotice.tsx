"use client";

import { useEffect, useState } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { getMarketSession } from "@/lib/marketSession";

/**
 * How long a connection state must hold before the banner is allowed to mount
 * or unmount.
 *
 * This banner is a sibling of <main> in the app shell, so every mount/unmount
 * pushes the entire page down or up by its height (~37px at >=md). The socket
 * starts at `disconnected` with a null snapshot, which means the pessimistic
 * "not connected" banner rendered on first paint and then retracted a second
 * later once the real state arrived — and re-toggled on each reconnect cycle.
 * Instrumented on production /research: three ~37px shifts inside 200s, worth
 * 71% of the page's total CLS.
 *
 * Committing only sustained changes keeps the page geometrically stable. It
 * does not change what the banner says, how it looks, or when a genuine
 * outage is surfaced — an outage lasting longer than this window still shows.
 */
const SHOW_AFTER_MS = 3000;

/**
 * Hiding is held longer than showing on purpose. A flapping backend would
 * otherwise remove the banner and re-add it on the next blip, reintroducing the
 * very shift this guards against. Lingering briefly after recovery is harmless;
 * bouncing the page is not.
 */
const HIDE_AFTER_MS = 15000;

type Variant =
  | { kind: "none" }
  | { kind: "stale-age" }
  | { kind: "reconnecting" }
  | { kind: "info"; message: string };

const bannerStyle = (bg: string, border: string): React.CSSProperties => ({
  background: bg,
  borderBottom: `1px solid ${border}`,
  color: "var(--text-secondary)",
});

/**
 * Stream / snapshot context: prefer verified snapshot language over “degraded” jargon.
 * Suppresses banners during brief WS reconnect when a prior snapshot is still in memory.
 */
export default function BackendStatusNotice() {
  const {
    snapshot,
    status,
    snapshotLikelyStale,
    globalStateVersion,
    forcedResyncs,
    rejectedOutOfOrder,
    snapshotReceivedAt,
  } = useEngineSocket();
  const session = getMarketSession();
  const hasFreshPath = status === "connected" || status === "polling";
  const engineHint = snapshot?.engine_running === true || snapshot?.engine_live === true;
  const staleData = snapshot?.stale === true;

  // ── Which banner does the CURRENT state call for? ────────────────────────
  // Structure only. Live counters (snapshot age, state version, last tick) are
  // read at render time below, so their per-second churn never restarts the
  // settle timer.
  let desired: Variant;
  if (snapshot && (status === "connecting" || status === "disconnected")) {
    // Silent recovery: do not flash yellow bars while the socket cycle runs but
    // data is still on screen
    desired = { kind: "none" };
  } else if (hasFreshPath && engineHint && !staleData) {
    desired = { kind: "none" };
  } else if (snapshotLikelyStale && hasFreshPath && snapshot) {
    desired = { kind: "stale-age" };
  } else if (staleData && hasFreshPath && snapshot) {
    desired = { kind: "reconnecting" };
  } else {
    const closed = session === "CLOSED";
    desired = {
      kind: "info",
      message: !hasFreshPath
        ? closed
          ? "Market is closed. Live engine stream may be idle; research data below still loads from the API when the backend is up."
          : "Connecting to the live data stream. Panels below use the REST API and may populate before the stream connects."
        : "Engine snapshot not active yet. Open research and scans still work when the Railway backend is reachable.",
    };
  }

  // ── Commit a change only once the desired banner has held long enough ───
  // Starts at "none" so first paint never renders a banner we are about to
  // retract.
  const [shown, setShown] = useState<Variant>({ kind: "none" });
  const desiredKey = desired.kind === "info" ? `info:${desired.message}` : desired.kind;
  const shownKey = shown.kind === "info" ? `info:${shown.message}` : shown.kind;

  useEffect(() => {
    if (desiredKey === shownKey) return;
    // desiredKey fully describes the variant, so it can be rebuilt here without
    // holding a ref to the render-time object.
    const t = setTimeout(() => {
      setShown(
        desiredKey.startsWith("info:")
          ? { kind: "info", message: desiredKey.slice("info:".length) }
          : { kind: desiredKey as "none" | "stale-age" | "reconnecting" },
      );
    }, desiredKey === "none" ? HIDE_AFTER_MS : SHOW_AFTER_MS);
    return () => clearTimeout(t);
  }, [desiredKey, shownKey]);

  // `Date.now()` during render is impure (pre-existing react-hooks/purity error)
  // and lets this banner's text change on any incidental re-render. Tick it
  // explicitly instead, and only while the diagnostics suffix is on screen.
  const showsDiagnostics =
    shown.kind === "info" && hasFreshPath && globalStateVersion > 0 && snapshotReceivedAt > 0;
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!showsDiagnostics) return;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [showsDiagnostics]);

  if (shown.kind === "none") return null;

  if (shown.kind === "stale-age") {
    return (
      <div
        className="px-3 py-2 text-center text-xs md:text-sm shrink-0"
        style={bannerStyle("rgba(245,158,11,0.14)", "rgba(245,158,11,0.3)")}
        role="status"
      >
        <strong style={{ color: "var(--warning)" }}>Snapshot age high</strong> — requesting resync
        {snapshot?._snapshot_age_ms != null ? ` · ~${Math.round(snapshot._snapshot_age_ms / 1000)}s behind clock` : ""}
      </div>
    );
  }

  if (shown.kind === "reconnecting") {
    return (
      <div
        className="px-3 py-2 text-center text-xs md:text-sm shrink-0"
        style={bannerStyle("rgba(245,158,11,0.12)", "rgba(245,158,11,0.28)")}
        role="status"
      >
        <strong style={{ color: "var(--warning)" }}>Realtime feed reconnecting</strong> — showing latest verified market snapshot
        {snapshot?.snapshot_time ? ` · ${snapshot.snapshot_time}` : ""}
      </div>
    );
  }

  return (
    <div
      className="px-3 py-2 text-center text-xs md:text-sm shrink-0"
      style={bannerStyle("rgba(245,158,11,0.08)", "rgba(245,158,11,0.2)")}
      role="status"
    >
      {shown.message}
      {hasFreshPath && globalStateVersion > 0 ? (
        <span style={{ opacity: 0.75 }}>
          {" "}
          · unified state v{globalStateVersion}
          {forcedResyncs > 0 ? ` · resyncs ${forcedResyncs}` : ""}
          {rejectedOutOfOrder > 0 ? ` · ordered stream (${rejectedOutOfOrder} stale frames dropped)` : ""}
          {snapshotReceivedAt > 0 ? (
            <>
              {" "}
              · last tick{" "}
              {Math.max(0, Math.min(599, Math.round((now - snapshotReceivedAt) / 1000)))}
              s ago
            </>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}
