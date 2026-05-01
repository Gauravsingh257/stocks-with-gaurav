"use client";

/**
 * MiniChart — real OHLC area chart powered by TradingView Lightweight Charts v5.
 *
 * Props:
 *   symbol    – NSE ticker (e.g. "RELIANCE") fed to GET /api/chart/{symbol}
 *   direction – "BUY" | "SELL" (controls line colour)
 *   height    – container height in px (default 72)
 *   entry     – optional entry price level rendered as a dashed horizontal line
 *   stop      – optional stop-loss level (red dashed)
 *   target    – optional target level (green dashed)
 *
 * Fake data generators have been removed. Falls back to a loading skeleton while
 * the first fetch is in-flight, and a muted "No data" placeholder on error / empty.
 */

import { useEffect, useRef } from "react";
import { useChartData } from "../_lib/useChartData";
import type { OHLCBar } from "../_lib/useChartData";

interface Props {
  symbol: string;
  direction: "BUY" | "SELL";
  height?: number;
  entry?: number | null;
  stop?: number | null;
  target?: number | null;
}

export default function MiniChart({ symbol, direction, height = 72, entry, stop, target }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<any>(null);

  const { bars, loading, error } = useChartData(symbol, "5m");

  const isUp = direction === "BUY";
  const lineColor = isUp ? "#00e096" : "#ff4757";
  const areaTopColor = isUp ? "rgba(0,224,150,0.22)" : "rgba(255,71,87,0.22)";

  // Init chart once
  useEffect(() => {
    if (typeof window === "undefined" || !containerRef.current) return;

    let chart: ReturnType<typeof import("lightweight-charts").createChart> | null = null;

    (async () => {
      const { createChart, AreaSeries, LineStyle } = await import("lightweight-charts");
      if (!containerRef.current) return;

      chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth || 220,
        height,
        layout: {
          background: { color: "transparent" },
          textColor: "rgba(136,153,187,0.8)",
          fontSize: 9,
        },
        grid: {
          vertLines: { visible: false },
          horzLines: { color: "rgba(255,255,255,0.04)" },
        },
        crosshair: { mode: 0 /* None */ },
        rightPriceScale: {
          visible: true,
          borderVisible: false,
          scaleMargins: { top: 0.1, bottom: 0.1 },
          minimumWidth: 48,
        },
        timeScale: {
          visible: false,
          borderVisible: false,
          fixLeftEdge: true,
          fixRightEdge: true,
        },
        handleScroll: false,
        handleScale: false,
      });

      const series = chart.addSeries(AreaSeries, {
        lineColor,
        topColor: areaTopColor,
        bottomColor: "transparent",
        lineWidth: 2,
        crosshairMarkerVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      seriesRef.current = series;
      chartRef.current = chart;

      // Price level lines
      if (entry != null) {
        series.createPriceLine({
          price: entry,
          color: "#00d4ff",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "E",
        });
      }
      if (stop != null) {
        series.createPriceLine({
          price: stop,
          color: "#ff4757",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "SL",
        });
      }
      if (target != null) {
        series.createPriceLine({
          price: target,
          color: "#00e096",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "T",
        });
      }

      // ResizeObserver keeps chart filling its container
      const ro = new ResizeObserver(() => {
        if (containerRef.current && chart) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      });
      if (containerRef.current) ro.observe(containerRef.current);

      return () => {
        ro.disconnect();
        chart?.remove();
        chartRef.current = null;
        seriesRef.current = null;
      };
    })();

    return () => {
      // Cleanup handled inside async IIFE
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, lineColor, areaTopColor]);

  // Push new bars whenever data changes
  useEffect(() => {
    if (!seriesRef.current || !bars.length) return;
    const mapped = bars.map((b: OHLCBar) => ({ time: b.time as unknown as import("lightweight-charts").UTCTimestamp, value: b.close }));
    seriesRef.current.setData(mapped);
    chartRef.current?.timeScale().fitContent();
  }, [bars]);

  // Loading skeleton
  if (loading) {
    return (
      <div
        style={{
          height,
          borderRadius: 8,
          background: "linear-gradient(90deg, rgba(255,255,255,0.03) 25%, rgba(255,255,255,0.07) 50%, rgba(255,255,255,0.03) 75%)",
          backgroundSize: "200% 100%",
          animation: "shimmer 1.4s linear infinite",
        }}
      />
    );
  }

  // Error / empty fallback — show a muted dash so card layout doesn't collapse
  if (error || !bars.length) {
    return (
      <div
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 8,
          border: "1px dashed rgba(255,255,255,0.08)",
          fontSize: "0.6rem",
          color: "var(--text-dim)",
          letterSpacing: 0.5,
        }}
      >
        {error ? "Chart unavailable" : "Awaiting data…"}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      style={{
        height,
        width: "100%",
        borderRadius: 8,
        overflow: "hidden",
        position: "relative",
      }}
    />
  );
}
