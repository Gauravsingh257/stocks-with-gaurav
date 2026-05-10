/**
 * Lightweight browser-side trust metrics — flushed to POST /api/system/client-trust-metrics.
 */

const STORAGE_PREFIX = "dash_ctrust:";
const FLUSH_MS = 45_000;

const pending: Record<string, number> = {};
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function bump(key: string, delta = 1): void {
  pending[key] = (pending[key] || 0) + delta;
  scheduleFlush();
}

function scheduleFlush(): void {
  if (flushTimer != null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flushTrustMetrics();
  }, FLUSH_MS);
}

export function trustSessionBump(key: string, delta = 1): void {
  try {
    if (typeof sessionStorage === "undefined") return;
    const k = STORAGE_PREFIX + key;
    const prev = parseInt(sessionStorage.getItem(k) || "0", 10) || 0;
    sessionStorage.setItem(k, String(prev + delta));
  } catch {
    /* ignore */
  }
}

export async function flushTrustMetrics(): Promise<void> {
  const counters: Record<string, number> = { ...pending };
  for (const [k, v] of Object.entries(counters)) {
    if (!v) delete counters[k];
  }
  try {
    if (typeof sessionStorage !== "undefined") {
      for (let i = 0; i < sessionStorage.length; i++) {
        const sk = sessionStorage.key(i);
        if (!sk?.startsWith(STORAGE_PREFIX)) continue;
        const shortKey = sk.slice(STORAGE_PREFIX.length);
        const raw = sessionStorage.getItem(sk);
        const n = parseInt(raw || "0", 10);
        if (n > 0) {
          counters[shortKey] = (counters[shortKey] || 0) + n;
          sessionStorage.removeItem(sk);
        }
      }
    }
  } catch {
    /* ignore */
  }

  if (!Object.keys(counters).length) return;

  for (const k of Object.keys(pending)) delete pending[k];

  const base = (process.env.NEXT_PUBLIC_BACKEND_URL || "").trim().replace(/\/$/, "");
  if (!base) return;

  try {
    await fetch(`${base}/api/system/client-trust-metrics`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ counters, ts: Date.now() }),
      keepalive: true,
    });
  } catch {
    /* merge back on failure */
    for (const [k, v] of Object.entries(counters)) {
      pending[k] = (pending[k] || 0) + v;
    }
  }
}

export function recordWsClose(): void {
  bump("ws_close");
  trustSessionBump("ws_close");
}

export function recordWsReconnect(): void {
  bump("ws_reconnect");
  trustSessionBump("ws_reconnect");
}

export function recordTabResumeReconnect(): void {
  bump("tab_resume_ws_retry");
  trustSessionBump("tab_resume_ws_retry");
}

export function recordReconcileReject(kind: "stale_gv" | "out_of_order" | "stale_snapshot_age"): void {
  bump(`reconcile_${kind}`);
  trustSessionBump(`reconcile_${kind}`);
}
