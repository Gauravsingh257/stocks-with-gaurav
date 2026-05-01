"use client";

/**
 * useChartData — fetches real OHLC bars for a symbol from GET /api/chart/{symbol}
 * and splices in live-tick updates received over the main /ws WebSocket.
 *
 * Behaviour:
 *   • Fetches on mount (GET /api/chart/{symbol}?interval=5m)
 *   • Refetches every REFRESH_MS when tab is visible
 *   • Subscribes to window-level "ws:ltp" CustomEvent (dispatched by a shared
 *     WS listener) to push real-time close updates for the current candle
 *   • Returns { bars, loading, error, lastPrice }
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getBackendBase } from "@/lib/api";

export interface OHLCBar {
  time: number;   // Unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
}

interface ChartDataState {
  bars: OHLCBar[];
  loading: boolean;
  error: string | null;
  lastPrice: number | null;
}

const REFRESH_MS = 60_000; // re-fetch every 60s (chart cache TTL is also 60s)

// ── Shared main-WS singleton ──────────────────────────────────────────────────
// We open one WS per page (not per card) and dispatch CustomEvents per symbol.
let _ws: WebSocket | null = null;
let _wsRefCount = 0;
let _wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

function _ensureMainWs() {
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;
  const base = getBackendBase();
  if (!base) return;
  try {
    const url = new URL("/ws", base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    _ws = new WebSocket(url.toString());
    _ws.addEventListener("message", (ev) => {
      try {
        const msg = JSON.parse(ev.data as string);
        if (msg.type === "ltp" && msg.data) {
          // Broadcast to all hooks via CustomEvent
          window.dispatchEvent(new CustomEvent("ws:ltp", { detail: msg.data }));
        }
      } catch {
        // ignore parse errors
      }
    });
    _ws.addEventListener("close", () => {
      if (_wsRefCount > 0) {
        // auto-reconnect after 3s
        if (_wsReconnectTimer) clearTimeout(_wsReconnectTimer);
        _wsReconnectTimer = setTimeout(_ensureMainWs, 3_000);
      }
    });
  } catch {
    // WS not available (SSR / no base URL)
  }
}

function _releaseMainWs() {
  _wsRefCount = Math.max(0, _wsRefCount - 1);
  if (_wsRefCount === 0 && _ws) {
    _ws.close();
    _ws = null;
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────
export function useChartData(
  symbol: string,
  interval = "5m",
): ChartDataState {
  const [bars, setBars] = useState<OHLCBar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastPrice, setLastPrice] = useState<number | null>(null);

  const base = getBackendBase();
  const mountedRef = useRef(true);

  // Fetch OHLC bars
  const fetchBars = useCallback(async () => {
    if (!base || !symbol) return;
    try {
      const res = await fetch(`${base}/api/chart/${encodeURIComponent(symbol)}?interval=${interval}`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`/api/chart → ${res.status}`);
      const data = await res.json();
      if (!mountedRef.current) return;
      const newBars: OHLCBar[] = Array.isArray(data?.bars) ? data.bars : [];
      setBars(newBars);
      setError(null);
      if (newBars.length > 0) {
        setLastPrice(newBars[newBars.length - 1].close);
      }
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : "fetch failed");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [base, symbol, interval]);

  // Initial fetch + polling refresh
  useEffect(() => {
    mountedRef.current = true;
    fetchBars();
    const timer = setInterval(fetchBars, REFRESH_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [fetchBars]);

  // Live tick via shared WS → splice last bar's close
  useEffect(() => {
    _wsRefCount++;
    _ensureMainWs();

    function onLtp(ev: Event) {
      const detail = (ev as CustomEvent<Record<string, unknown>>).detail;
      // LTP payload keys: { SYMBOL: price } or { symbol: "NIFTY", ltp: 22500 }
      let price: number | null = null;
      // Try nested object key (Kite LTP format: { "NSE:SYMBOL": { last_price: N } })
      for (const [key, val] of Object.entries(detail)) {
        const normalKey = key.replace(/^(NSE:|BSE:)/, "").toUpperCase();
        if (normalKey === symbol.toUpperCase()) {
          if (typeof val === "object" && val !== null && "last_price" in val) {
            price = Number((val as { last_price: unknown }).last_price);
          } else if (typeof val === "number") {
            price = val;
          }
          break;
        }
        // Flat format: { symbol: "NIFTY", ltp: 22500 }
        if (
          "symbol" in detail &&
          String(detail.symbol).toUpperCase() === symbol.toUpperCase() &&
          "ltp" in detail
        ) {
          price = Number(detail.ltp);
          break;
        }
      }
      if (price == null || !Number.isFinite(price)) return;

      setLastPrice(price);
      setBars((prev) => {
        if (!prev.length) return prev;
        const last = prev[prev.length - 1];
        const updated: OHLCBar = {
          ...last,
          high: Math.max(last.high, price!),
          low: Math.min(last.low, price!),
          close: price!,
        };
        return [...prev.slice(0, -1), updated];
      });
    }

    window.addEventListener("ws:ltp", onLtp);
    return () => {
      window.removeEventListener("ws:ltp", onLtp);
      _releaseMainWs();
    };
  }, [symbol]);

  return { bars, loading, error, lastPrice };
}
