"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";

const TRADINGVIEW_SCRIPT_ID = "tradingview-widget-script";
const TRADINGVIEW_SCRIPT_SRC = "https://s3.tradingview.com/tv.js";
const UNSUPPORTED_NSE_SYMBOLS = new Set(["DATAPATTNS"]);

declare global {
  interface Window {
    TradingView?: {
      widget: new (config: Record<string, unknown>) => { onChartReady?: (callback: () => void) => void };
    };
  }
}

export function mapSymbol(symbol: string) {
  const clean = symbol.replace("NSE:", "").trim().toUpperCase();
  return clean;
}

function tradingViewUrl(mappedSymbol: string) {
  return `https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(mappedSymbol)}`;
}

function loadTradingViewScript() {
  if (typeof window === "undefined") return Promise.reject(new Error("TradingView can only load in the browser"));
  if (window.TradingView?.widget) return Promise.resolve();

  const existing = document.getElementById(TRADINGVIEW_SCRIPT_ID) as HTMLScriptElement | null;
  if (existing) {
    return new Promise<void>((resolve, reject) => {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("TradingView script failed to load")), { once: true });
    });
  }

  return new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.id = TRADINGVIEW_SCRIPT_ID;
    script.src = TRADINGVIEW_SCRIPT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("TradingView script failed to load"));
    document.head.appendChild(script);
  });
}

function ChartFallback({ mappedSymbol, error }: { mappedSymbol: string; error: string | null }) {
  return (
    <div
      style={{
        minHeight: 430,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        borderRadius: 10,
        border: "1px solid rgba(255,255,255,0.08)",
        background: "#0a0e17",
        color: "var(--text-secondary)",
        textAlign: "center",
        padding: 20,
      }}
    >
      <div style={{ color: "var(--text-primary)", fontWeight: 800 }}>Chart not available for this stock</div>
      {error ? <div style={{ maxWidth: 460, fontSize: "0.78rem", color: "var(--text-dim)" }}>{error}</div> : null}
      <a
        href={tradingViewUrl(mappedSymbol)}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          padding: "8px 12px",
          borderRadius: 6,
          border: "1px solid rgba(41,98,255,0.35)",
          background: "rgba(41,98,255,0.12)",
          color: "#5b9cf6",
          fontSize: "0.82rem",
          fontWeight: 700,
          textDecoration: "none",
        }}
      >
        Open in TradingView <ExternalLink size={14} />
      </a>
    </div>
  );
}

export function TradingViewStockWidget({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const containerId = useMemo(() => `tv-chart-${mapSymbol(symbol).replace(/[^A-Z0-9_-]/g, "-")}`, [symbol]);
  const mappedSymbol = useMemo(() => mapSymbol(symbol), [symbol]);
  const [fallbackError, setFallbackError] = useState<string | null>(null);
  const [widgetReady, setWidgetReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let readyTimer: number | undefined;

    function showFallback(error: unknown) {
      if (readyTimer) window.clearTimeout(readyTimer);
      const message = error instanceof Error ? error.message : String(error || "TradingView widget failed to load");
      console.warn("[TradingViewWidget] fallback", {
        symbol,
        mappedSymbol,
        error: message,
      });
      if (!cancelled) setFallbackError(message);
    }

    async function loadWidget() {
      console.info("[TradingViewWidget] load", { symbol, mappedSymbol });

      if (!mappedSymbol) {
        showFallback(new Error("Missing stock symbol"));
        return;
      }

      if (UNSUPPORTED_NSE_SYMBOLS.has(mappedSymbol)) {
        showFallback(new Error(`${mappedSymbol} is not supported by the embedded TradingView widget`));
        return;
      }

      try {
        setFallbackError(null);
        setWidgetReady(false);
        await loadTradingViewScript();
        if (cancelled || !containerRef.current || !window.TradingView?.widget) return;

        containerRef.current.innerHTML = "";
        const widgetHost = document.createElement("div");
        widgetHost.id = containerId;
        widgetHost.style.width = "100%";
        widgetHost.style.height = "430px";
        containerRef.current.appendChild(widgetHost);

        readyTimer = window.setTimeout(() => {
          showFallback(new Error("TradingView did not confirm chart readiness"));
        }, 8000);

        const widget = new window.TradingView.widget({
          autosize: true,
          container_id: containerId,
          symbol: mappedSymbol,
          interval: "D",
          exchange: "NSE",
          locale: "en",
          theme: "dark",
          style: "1",
          hide_top_toolbar: false,
          hide_side_toolbar: false,
          allow_symbol_change: false,
        });

        if (typeof widget.onChartReady === "function") {
          widget.onChartReady(() => {
            if (readyTimer) window.clearTimeout(readyTimer);
            if (!cancelled) setWidgetReady(true);
          });
        } else {
          if (readyTimer) window.clearTimeout(readyTimer);
          setWidgetReady(true);
        }
      } catch (error) {
        showFallback(error);
      }
    }

    loadWidget();

    return () => {
      cancelled = true;
      if (readyTimer) window.clearTimeout(readyTimer);
      if (containerRef.current) containerRef.current.innerHTML = "";
    };
  }, [containerId, mappedSymbol, symbol]);

  if (fallbackError) {
    return <ChartFallback mappedSymbol={mappedSymbol} error={fallbackError} />;
  }

  return (
    <div style={{ position: "relative", width: "100%", height: 430, borderRadius: 10, overflow: "hidden", background: "#0a0e17" }}>
      {!widgetReady && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 2,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#5b9cf6",
            fontSize: "0.85rem",
            background: "#0a0e17",
          }}
        >
          Loading chart...
        </div>
      )}
      <div ref={containerRef} style={{ width: "100%", height: 430, opacity: widgetReady ? 1 : 0 }} />
    </div>
  );
}