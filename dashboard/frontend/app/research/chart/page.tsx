"use client";

import { useEffect, useRef, useState, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Share2, Check, Tag } from "lucide-react";
import Link from "next/link";
import {
  createChart,
  type IChartApi,
  CandlestickSeries,
  HistogramSeries,
  ColorType,
  LineStyle,
  CrosshairMode,
} from "lightweight-charts";
import { api, type ResearchChartData } from "@/lib/api";
import { SmcZonesPrimitive, type SmcZone, type ZoneOptions } from "@/lib/smcZonesPrimitive";

function fmt(v: number) {
  return v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Compact label for mobile/narrow screens (Task 7).
function abbreviate(label: string): string {
  let s = label
    .replace(/Order Block/gi, "OB")
    .replace(/Fair Value Gap/gi, "FVG")
    .replace(/Weekly/gi, "W")
    .replace(/Bullish/gi, "Bull")
    .replace(/Bearish/gi, "Bear")
    .replace(/Structure/gi, "Struct")
    .replace(/Demand/gi, "Dmd")
    .replace(/Supply/gi, "Sup")
    .replace(/\s+/g, " ")
    .trim();
  s = s.replace(/^W /, "W-");
  return s;
}
function timeframeOf(label: string): string {
  return /weekly/i.test(label) ? "Weekly" : "Daily";
}
function buildZones(zones: ResearchChartData["zones"]): SmcZone[] {
  return zones.map((z, i) => ({
    id: `${z.label}-${i}`,
    label: z.label,
    short: abbreviate(z.label),
    top: z.top,
    bottom: z.bottom,
    fill: z.color,
    border: z.border_color,
  }));
}

function setupBadgeColor(setup: string): string {
  if (setup.includes("STRONG_BULL")) return "#00d18c";
  if (setup.includes("BULLISH")) return "#5b9cf6";
  if (setup.includes("BEARISH")) return "#ff4e6a";
  return "#f0c060";
}

function SkeletonBar({ w, h }: { w: string; h: number }) {
  return (
    <div style={{
      width: w, height: h, borderRadius: 4,
      background: "linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0.04) 75%)",
      backgroundSize: "200% 100%",
      animation: "shimmer 1.5s infinite",
    }} />
  );
}

export default function ResearchChartPage() {
  return (
    <Suspense fallback={<ChartSkeleton />}>
      <ChartContent />
    </Suspense>
  );
}

function ChartSkeleton() {
  return (
    <div className="h-screen-dvh" style={{ display: "flex", flexDirection: "column", background: "#0a0e17" }}>
      <style>{`@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
      <div style={{ padding: "12px 20px", borderBottom: "1px solid rgba(255,255,255,0.08)", display: "flex", gap: 16, alignItems: "center", background: "#0d1117" }}>
        <SkeletonBar w="60px" h={16} />
        <SkeletonBar w="120px" h={22} />
        <SkeletonBar w="60px" h={18} />
      </div>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ color: "#5b9cf6", fontSize: "0.9rem" }}>Loading chart...</div>
      </div>
    </div>
  );
}

function ChartContent() {
  const searchParams = useSearchParams();
  const symbol = searchParams.get("symbol") || "";
  const horizon = searchParams.get("horizon") || "SWING";

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const primitiveRef = useRef<SmcZonesPrimitive | null>(null);
  const optsRef = useRef<ZoneOptions>({ showLabels: true, hoveredId: null, mobile: false });
  const zonesRef = useRef<SmcZone[]>([]);
  const currentPriceRef = useRef<number>(0);
  const showLabelsRef = useRef<boolean>(true);
  const hoverIdsRef = useRef<{ chart: string | null; legend: string | null }>({ chart: null, legend: null });
  const [data, setData] = useState<ResearchChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  // Which zone's legend row to highlight (driven by chart hover).
  const [chartHoverId, setChartHoverId] = useState<string | null>(null);

  // Persisted "Show Zone Labels" setting (Task 6).
  useEffect(() => {
    try {
      const v = localStorage.getItem("chart:showLabels");
      if (v === "0") setShowLabels(false);
    } catch { /* ignore */ }
  }, []);

  // Recompute the effective hovered zone (chart hover wins over legend hover)
  // and repaint the canvas only — no React re-render of the chart.
  const applyHover = useCallback(() => {
    optsRef.current.hoveredId = hoverIdsRef.current.chart ?? hoverIdsRef.current.legend;
    primitiveRef.current?.requestUpdate();
  }, []);

  const setLegendHover = useCallback((id: string | null) => {
    hoverIdsRef.current.legend = id;
    applyHover();
  }, [applyHover]);

  // Reflect the label toggle into the live primitive.
  useEffect(() => {
    showLabelsRef.current = showLabels;
    optsRef.current.showLabels = showLabels;
    primitiveRef.current?.requestUpdate();
    try { localStorage.setItem("chart:showLabels", showLabels ? "1" : "0"); } catch { /* ignore */ }
  }, [showLabels]);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    api
      .researchChartData(symbol.replace("NSE:", ""), horizon)
      .then(setData)
      .catch((e) => setError(e.message || "Failed to load chart data"))
      .finally(() => setLoading(false));
  }, [symbol, horizon]);

  const handleShare = useCallback(() => {
    navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, []);

  const renderChart = useCallback(() => {
    if (!data || !chartContainerRef.current) return;
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const container = chartContainerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#0a0e17" },
        textColor: "#94a3b8",
        fontSize: 12,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.03)" },
        horzLines: { color: "rgba(255,255,255,0.03)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.1)",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.1)",
        timeVisible: false,
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#00d18c",
      downColor: "#ff4e6a",
      borderDownColor: "#ff4e6a",
      borderUpColor: "#00d18c",
      wickDownColor: "#ff4e6a",
      wickUpColor: "#00d18c",
    });

    candleSeries.setData(
      data.candles.map((c) => ({
        time: c.time as string,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });

    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    volumeSeries.setData(
      data.candles.map((c) => ({
        time: c.time as string,
        value: c.volume,
        color: c.close >= c.open ? "rgba(0,209,140,0.15)" : "rgba(255,78,106,0.15)",
      }))
    );

    for (const level of data.levels) {
      const lineStyle =
        level.style === "dashed" ? LineStyle.Dashed
        : level.style === "dotted" ? LineStyle.Dotted
        : LineStyle.Solid;

      candleSeries.createPriceLine({
        price: level.price,
        color: level.color,
        lineWidth: level.type === "entry" ? 2 : 1,
        lineStyle,
        axisLabelVisible: true,
        title: level.label,
      });
    }

    // Current price (last close) — drives zone status in the tooltip.
    const lastCandle = data.candles[data.candles.length - 1];
    currentPriceRef.current = lastCandle ? lastCandle.close : 0;

    // SMC zones + inline labels, drawn by one primitive (bands behind candles,
    // labels above). It reads live options (labels on/off, hover, mobile).
    const zones = buildZones(data.zones);
    zonesRef.current = zones;
    optsRef.current = { showLabels: showLabelsRef.current, hoveredId: null, mobile: container.clientWidth < 768 };
    const zonesPrimitive = new SmcZonesPrimitive(zones, () => optsRef.current);
    type PrimitiveArg = Parameters<typeof candleSeries.attachPrimitive>[0];
    candleSeries.attachPrimitive(zonesPrimitive as unknown as PrimitiveArg);
    primitiveRef.current = zonesPrimitive;

    // Hover tooltip + zone↔legend sync via the crosshair (no per-frame React
    // state; tooltip is positioned imperatively).
    const onCrosshair = (param: { point?: { x: number; y: number }; time?: unknown }) => {
      const tip = tooltipRef.current;
      const pt = param.point;
      if (!pt) {
        if (tip) tip.style.display = "none";
        if (hoverIdsRef.current.chart !== null) { hoverIdsRef.current.chart = null; setChartHoverId(null); applyHover(); }
        return;
      }
      const price = candleSeries.coordinateToPrice(pt.y);
      const zone = price == null ? undefined : zonesRef.current.find(
        (z) => price <= Math.max(z.top, z.bottom) && price >= Math.min(z.top, z.bottom),
      );
      if (!zone) {
        if (tip) tip.style.display = "none";
        if (hoverIdsRef.current.chart !== null) { hoverIdsRef.current.chart = null; setChartHoverId(null); applyHover(); }
        return;
      }
      const hi = Math.max(zone.top, zone.bottom);
      const lo = Math.min(zone.top, zone.bottom);
      const cp = currentPriceRef.current;
      const status = cp > hi ? "Price is currently ABOVE this zone"
        : cp < lo ? "Price is currently BELOW this zone"
        : "Price is currently INSIDE this zone";
      if (tip) {
        const range = hi === lo ? `₹${fmt(hi)}` : `₹${fmt(lo)} – ₹${fmt(hi)}`;
        tip.innerHTML = `<div style="font-weight:700;color:#fff">${zone.label}</div>`
          + `<div style="opacity:.75;margin-top:2px">${timeframeOf(zone.label)} · ${range}</div>`
          + `<div style="margin-top:3px;color:${zone.border}">${status}</div>`;
        tip.style.borderColor = zone.border;
        tip.style.display = "block";
        const tw = tip.offsetWidth || 160;
        tip.style.left = `${Math.min(pt.x + 14, container.clientWidth - tw - 8)}px`;
        tip.style.top = `${pt.y + 14}px`;
      }
      if (hoverIdsRef.current.chart !== zone.id) {
        hoverIdsRef.current.chart = zone.id;
        setChartHoverId(zone.id);
        applyHover();
      }
    };
    chart.subscribeCrosshairMove(onCrosshair);

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (chartRef.current && container) {
        chartRef.current.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight,
        });
        const mobile = container.clientWidth < 768;
        if (optsRef.current.mobile !== mobile) {
          optsRef.current.mobile = mobile;
          primitiveRef.current?.requestUpdate();
        }
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [data, applyHover]);

  useEffect(() => {
    const cleanup = renderChart();
    return cleanup;
  }, [renderChart]);

  if (!symbol) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>
        No symbol specified. Go back to{" "}
        <Link href="/research" style={{ color: "#5b9cf6" }}>Research Center</Link>
      </div>
    );
  }

  return (
    <div className="h-screen-dvh" style={{ display: "flex", flexDirection: "column", background: "#0a0e17" }}>
      <style>{`@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
      {/* Header */}
      <div style={{
        padding: "12px 20px",
        borderBottom: "1px solid rgba(255,255,255,0.08)",
        display: "flex", alignItems: "center", gap: 16,
        background: "#0d1117", flexShrink: 0, flexWrap: "wrap",
      }}>
        <Link href="/research" style={{ display: "flex", alignItems: "center", gap: 6, color: "#5b9cf6", textDecoration: "none", fontSize: "0.82rem", fontWeight: 500 }}>
          <ArrowLeft size={16} /> Research
        </Link>
        <div className="hidden md:block" style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontWeight: 700, fontSize: "1.1rem" }}>NSE:{symbol.replace("NSE:", "")}</span>
          <span style={{
            fontSize: "0.65rem", padding: "2px 8px", borderRadius: 4,
            background: horizon === "LONGTERM" ? "rgba(240,192,96,0.15)" : "rgba(91,156,246,0.15)",
            color: horizon === "LONGTERM" ? "#f0c060" : "#5b9cf6", fontWeight: 600,
          }}>
            {horizon}
          </span>
          {data?.setup && (
            <span style={{
              fontSize: "0.65rem", padding: "2px 8px", borderRadius: 4,
              background: `${setupBadgeColor(data.setup)}18`,
              color: setupBadgeColor(data.setup), fontWeight: 600,
            }}>
              {data.setup}
            </span>
          )}
          {data?.confidence ? (
            <span style={{ fontSize: "0.78rem", color: "#00ff88", fontWeight: 600 }}>
              {data.confidence.toFixed(1)}%
            </span>
          ) : null}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button
            onClick={() => setShowLabels((s) => !s)}
            title="Show or hide the labels drawn on each SMC zone"
            style={{
              display: "flex", alignItems: "center", gap: 6,
              fontSize: "0.75rem", color: showLabels ? "#00e096" : "#94a3b8",
              padding: "5px 12px", borderRadius: 6,
              background: showLabels ? "rgba(0,224,150,0.12)" : "rgba(255,255,255,0.05)",
              border: "1px solid " + (showLabels ? "rgba(0,224,150,0.3)" : "rgba(255,255,255,0.1)"),
              cursor: "pointer", fontWeight: 500,
            }}
          >
            {showLabels ? <Check size={13} /> : <Tag size={13} />}
            Zone Labels
          </button>
          <button
            onClick={handleShare}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              fontSize: "0.75rem", color: copied ? "#00e096" : "#94a3b8",
              padding: "5px 12px", borderRadius: 6,
              background: copied ? "rgba(0,224,150,0.12)" : "rgba(255,255,255,0.05)",
              border: "1px solid " + (copied ? "rgba(0,224,150,0.3)" : "rgba(255,255,255,0.1)"),
              cursor: "pointer", fontWeight: 500, transition: "all 0.2s",
            }}
          >
            {copied ? <Check size={13} /> : <Share2 size={13} />}
            {copied ? "Copied!" : "Share"}
          </button>
          <a
            href={`https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(symbol.replace("NSE:", ""))}&interval=D`}
            target="_blank" rel="noopener noreferrer"
            style={{
              fontSize: "0.75rem", color: "#5b9cf6", textDecoration: "none",
              padding: "5px 12px", borderRadius: 6,
              background: "rgba(41,98,255,0.12)", border: "1px solid rgba(41,98,255,0.3)", fontWeight: 500,
            }}
          >
            TradingView ↗
          </a>
        </div>
      </div>

      {/* Main content — responsive: row on desktop, column on mobile */}
      <div className="flex flex-col md:flex-row" style={{ flex: 1, overflow: "hidden" }}>
        {/* Chart area */}
        <div style={{ flex: 1, position: "relative", minHeight: 300 }}>
          {loading && (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#0a0e17", zIndex: 10 }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
                <div style={{ width: 32, height: 32, border: "3px solid rgba(91,156,246,0.2)", borderTopColor: "#5b9cf6", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                <div style={{ color: "#5b9cf6", fontSize: "0.85rem" }}>Loading chart data...</div>
              </div>
              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </div>
          )}
          {error && (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "#0a0e17", zIndex: 10 }}>
              <div style={{ color: "#ff4e6a", fontSize: "0.9rem" }}>{error}</div>
            </div>
          )}
          <div ref={chartContainerRef} style={{ width: "100%", height: "100%" }} />
          {/* Hover tooltip (positioned imperatively on crosshair move) */}
          <div
            ref={tooltipRef}
            style={{
              position: "absolute", display: "none", pointerEvents: "none", zIndex: 20,
              maxWidth: 220, padding: "7px 10px", borderRadius: 8,
              background: "rgba(13,17,23,0.94)", border: "1px solid rgba(255,255,255,0.15)",
              color: "#cbd5e1", fontSize: "0.72rem", lineHeight: 1.4,
              boxShadow: "0 8px 24px rgba(0,0,0,0.5)", backdropFilter: "blur(4px)",
            }}
          />
        </div>

        {/* Side panel — stacks below chart on mobile */}
        <div className="w-full md:w-[280px] md:flex-shrink-0" style={{
          borderLeft: "none", borderTop: "1px solid rgba(255,255,255,0.08)",
          background: "#0d1117", overflowY: "auto", padding: 16,
          display: "flex", flexDirection: "column", gap: 16,
        }}>
          <style>{`@media (min-width: 768px) { .side-panel-border { border-left: 1px solid rgba(255,255,255,0.08) !important; border-top: none !important; } }`}</style>

          {/* Trade Levels */}
          {data?.levels && data.levels.length > 0 && (
            <div>
              <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "#5b9cf6", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
                Trade Levels
              </div>
              <div style={{ display: "grid", gap: 6 }}>
                {data.levels.map((l, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 10px", borderRadius: 6, background: `${l.color}08`, borderLeft: `3px solid ${l.color}` }}>
                    <div>
                      <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", textTransform: "uppercase" }}>
                        {l.type === "sl" ? "Stop Loss" : l.type === "cmp" ? "CMP (Scan)" : l.type.startsWith("target") ? l.label.split(" ")[0] : "Entry"}
                        {l.entry_type && (
                          <span style={{ marginLeft: 6, fontSize: "0.6rem", padding: "1px 4px", borderRadius: 3, background: l.entry_type === "LIMIT" ? "rgba(41,98,255,0.15)" : "rgba(0,209,140,0.15)", color: l.entry_type === "LIMIT" ? "#5b9cf6" : "#00d18c" }}>
                            {l.entry_type}
                          </span>
                        )}
                      </div>
                    </div>
                    <div style={{ fontWeight: 600, fontSize: "0.82rem", color: l.color }}>₹{fmt(l.price)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Zones */}
          {data?.zones && data.zones.length > 0 && (
            <div>
              <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "#00d18c", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
                SMC Zones
              </div>
              <div style={{ display: "grid", gap: 6 }}>
                {data.zones.map((z, i) => {
                  const zid = `${z.label}-${i}`;
                  const active = chartHoverId === zid;
                  return (
                    <div
                      key={i}
                      onMouseEnter={() => setLegendHover(zid)}
                      onMouseLeave={() => setLegendHover(null)}
                      style={{
                        padding: "6px 10px", borderRadius: 6, background: z.color,
                        borderLeft: `3px solid ${z.border_color}`, cursor: "default",
                        outline: active ? `1px solid ${z.border_color}` : "none",
                        boxShadow: active ? `0 0 0 1px ${z.border_color}, 0 0 14px ${z.color}` : "none",
                        transition: "box-shadow 0.15s, outline 0.15s",
                      }}
                    >
                      <div style={{ fontSize: "0.68rem", color: z.border_color, fontWeight: 600, marginBottom: 2 }}>{z.label}</div>
                      <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                        {z.top === z.bottom ? <>₹{fmt(z.top)}</> : <>₹{fmt(z.bottom)} — ₹{fmt(z.top)}</>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Reasoning */}
          {data?.reasoning && (
            <div>
              <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
                AI Reasoning
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", lineHeight: 1.6, padding: "8px 10px", borderRadius: 6, background: "rgba(255,255,255,0.02)" }}>
                {data.reasoning}
              </div>
            </div>
          )}

          {/* Legend */}
          <div>
            <div style={{ fontSize: "0.68rem", fontWeight: 700, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
              Legend
            </div>
            <div style={{ display: "grid", gap: 4, fontSize: "0.72rem" }}>
              {[
                { color: "#2962ff", label: "Entry Level", style: "━━━" },
                { color: "#ff4757", label: "Stop Loss", style: "╌╌╌" },
                { color: "#00e096", label: "Target", style: "╌╌╌" },
                { color: "#f0c060", label: "CMP at Scan", style: "┈┈┈" },
                { color: "rgba(0,209,140,0.5)", label: "Order Block", style: "█" },
                { color: "rgba(91,156,246,0.5)", label: "Fair Value Gap", style: "█" },
              ].map((item, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ color: item.color, fontFamily: "monospace", fontSize: "0.7rem", width: 24 }}>{item.style}</span>
                  <span style={{ color: "var(--text-secondary)" }}>{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
