/**
 * lib/analytics.ts — one thin, SSR-safe façade over every analytics provider.
 *
 * Product-validation phase: we only OBSERVE. This forwards named events to
 * GA4 (gtag), Microsoft Clarity, and optionally PostHog — whichever are
 * configured via env. Never throws, never blocks; a missing provider is a
 * silent no-op so pages render identically with analytics off.
 *
 * Providers are gated by public env vars (set them in Vercel):
 *   NEXT_PUBLIC_GA4_ID        e.g. G-XXXXXXX
 *   NEXT_PUBLIC_CLARITY_ID    e.g. abcd1234
 *   NEXT_PUBLIC_POSTHOG_KEY   optional
 *   NEXT_PUBLIC_POSTHOG_HOST  optional (default https://app.posthog.com)
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    gtag?: (...args: any[]) => void;
    dataLayer?: any[];
    clarity?: (...args: any[]) => void;
    posthog?: { capture?: (e: string, p?: Record<string, any>) => void; identify?: (id: string, p?: Record<string, any>) => void };
  }
}

import { API_BASE } from "@/lib/api";

export const GA4_ID = process.env.NEXT_PUBLIC_GA4_ID || "";
export const CLARITY_ID = process.env.NEXT_PUBLIC_CLARITY_ID || "";
export const POSTHOG_KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY || "";
export const POSTHOG_HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://app.posthog.com";
export const ANALYTICS_ON = Boolean(GA4_ID || CLARITY_ID || POSTHOG_KEY);

type Props = Record<string, string | number | boolean | null | undefined>;

// ── First-party identity (for our own Product Health dashboard) ──────────────
function uuid(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch { /* noop */ }
  return "id-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

/** Stable per-browser id (localStorage) — unique users + retention. */
export function anonId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = localStorage.getItem("swg_anon_id");
    if (!id) { id = uuid(); localStorage.setItem("swg_anon_id", id); }
    return id;
  } catch { return ""; }
}

/** Per-session id (sessionStorage) — session duration + pages/session. */
function sessionId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = sessionStorage.getItem("swg_session_id");
    if (!id) { id = uuid(); sessionStorage.setItem("swg_session_id", id); }
    return id;
  } catch { return ""; }
}

function device(): string {
  if (typeof window === "undefined") return "server";
  const w = window.innerWidth;
  if (w < 768) return "mobile";
  if (w < 1024) return "tablet";
  return "desktop";
}

/** Fire-and-forget POST to our own event store. keepalive survives navigation. */
function postFirstParty(event: string, props: Props): void {
  if (typeof window === "undefined") return;
  try {
    const token = localStorage.getItem("swg-auth-token");
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    fetch(`${API_BASE}/api/product-analytics/event`, {
      method: "POST",
      headers,
      keepalive: true,
      body: JSON.stringify({
        event,
        anon_id: anonId(),
        session_id: sessionId(),
        path: window.location.pathname,
        device: device(),
        props,
      }),
    }).catch(() => { /* best effort */ });
  } catch { /* noop */ }
}

/** Fire a named product event to every configured provider + our own store. */
export function track(event: string, props: Props = {}): void {
  if (typeof window === "undefined") return;
  postFirstParty(event, props);
  try {
    window.gtag?.("event", event, props);
  } catch { /* noop */ }
  try {
    // Clarity custom events take a single string tag; attach a key prop as a
    // second custom tag when present so funnels stay filterable.
    window.clarity?.("event", event);
    if (props.symbol) window.clarity?.("set", "symbol", String(props.symbol));
  } catch { /* noop */ }
  try {
    window.posthog?.capture?.(event, props);
  } catch { /* noop */ }
}

/** Manual SPA page_view (GA4 is configured with send_page_view:false). */
export function pageview(path: string): void {
  if (typeof window === "undefined") return;
  postFirstParty("page_view", { path });
  try {
    if (GA4_ID) window.gtag?.("event", "page_view", { page_path: path, page_location: window.location.href });
  } catch { /* noop */ }
  try {
    window.posthog?.capture?.("$pageview", { path });
  } catch { /* noop */ }
}

/** Tie the current session to a user after login/signup. */
export function identify(userId: string | number, traits: Props = {}): void {
  if (typeof window === "undefined") return;
  const id = String(userId);
  try {
    if (GA4_ID) window.gtag?.("set", { user_id: id });
  } catch { /* noop */ }
  try {
    window.clarity?.("identify", id);
  } catch { /* noop */ }
  try {
    window.posthog?.identify?.(id, traits);
  } catch { /* noop */ }
}

/**
 * Map a route to a named "viewed/opened" event so the whole page-view funnel
 * is captured in ONE place, no per-page instrumentation. Returns null for
 * routes we don't name explicitly (still counted as a generic page_view).
 */
export function routeEvent(pathname: string): string | null {
  const p = pathname.replace(/\/+$/, "") || "/";
  const MAP: Record<string, string> = {
    "/command": "command_center_viewed",
    "/watchlist": "watchlist_opened",
    "/oi-intelligence": "oi_intelligence_opened",
    "/intelligence": "portfolio_viewed",
    "/research": "research_opened",
    "/screeners": "screeners_opened",
    "/terminal": "terminal_opened",
    "/analytics": "track_record_viewed",
    "/market-intelligence": "market_intel_opened",
    "/login": "login_page_viewed",
    "/register": "signup_page_viewed",
    "/": "landing_viewed",
  };
  if (MAP[p]) return MAP[p];
  if (p.startsWith("/intelligence")) return "portfolio_viewed";
  if (p.startsWith("/stock/")) return "stock_page_viewed";
  return null;
}
