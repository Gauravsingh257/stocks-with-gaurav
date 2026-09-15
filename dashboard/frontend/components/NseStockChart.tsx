"use client";

import { useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";
import { api, type ResearchChartCandle } from "@/lib/api";
import { tradingViewChartUrl } from "@/lib/tradingview";
import { inr, istDateTime, istDay, quoteSourceLabel } from "@/lib/stockFormat";
import { useTheme } from "@/components/ThemeProvider";

/**
 * Daily NSE price chart for /stock/<symbol>, drawn from our own data.
 *
 * It replaced an embedded TradingView widget that could never show NSE prices:
 * TradingView does not license NSE data to embedded widgets ("This symbol is
 * only available on TradingView"), even from our own domain. Handed a bare
 * ticker, the widget quietly charted whichever instrument it could show, so FCL
 * became ASX:FCL (FINEOS) and M&M or BAJAJ-AUTO showed an empty "symbol doesn't
 * exist" chart. Adding an NSE: prefix only changes the wrong chart into an empty
 * one.
 *
 * The candles come from /api/research/chart-data (`<SYMBOL>.NS` daily bars). The
 * caption's price is the page's current price — the price resolver quote carried
 * by the analysis response — unless the candles are from a later session than
 * that quote (e.g. an hour-old server render), in which case the latest close is
 * shown. Either way the caption says which one it is. A tradingview.com link
 * (which can show NSE) stays available for deeper charting.
 */

export interface PriceQuote {
  price: number;
  /** services/price_resolver source, e.g. "kite_live". */
  source?: string | null;
  /** When the quote was resolved (ISO timestamp). */
  asOf?: string | null;
}

type ChartState =
  | { status: "loading" }
  | { status: "ready"; candles: ResearchChartCandle[] }
  | { status: "empty" }
  | { status: "error" };

function dayLabel(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * The candle key ("YYYY-MM-DD") for a crosshair time. Depending on the library
 * path the time can arrive as the original string, a business-day object or a
 * UTC timestamp in seconds, so all three are accepted.
 */
function timeKey(time: Time | undefined): string | null {
  const pad = (n: number) => String(n).padStart(2, "0");
  if (typeof time === "string") return time;
  if (typeof time === "number") return new Date(time * 1000).toISOString().slice(0, 10);
  if (time && typeof time === "object" && "year" in time) {
    return `${time.year}-${pad(time.month)}-${pad(time.day)}`;
  }
  return null;
}

export default function NseStockChart({ symbol, quote }: { symbol: string; quote?: PriceQuote | null }) {
  const [state, setState] = useState<ChartState>({ status: "loading" });
  const hostRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLSpanElement>(null);
  const { theme } = useTheme();

  useEffect(() => {
    let cancelled = false;
    api
      .researchChartData(symbol)
      .then((data) => {
        if (cancelled) return;
        const candles = Array.isArray(data?.candles) ? data.candles : [];
        setState(candles.length ? { status: "ready", candles } : { status: "empty" });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const candles = state.status === "ready" ? state.candles : null;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !candles) return;

    const css = getComputedStyle(document.documentElement);
    const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    const up = token("--tone-good-fg", "#34d399");
    const down = token("--tone-weak-fg", "#fb7185");
    const line = theme === "light" ? "rgba(0,0,0,0.08)" : "rgba(255,255,255,0.06)";

    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: token("--text-secondary", "#8899bb"),
        fontSize: 12,
      },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: line, scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: { borderColor: line, timeVisible: false },
      localization: { priceFormatter: (price: number) => inr(price) },
    });

    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
    priceSeries.setData(
      candles.map((c) => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close })),
    );

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(52,211,153,0.3)" : "rgba(251,113,133,0.3)",
      })),
    );
    chart.timeScale().fitContent();

    // OHLC readout under the chart: the hovered day, or the latest one.
    const byDay = new Map(candles.map((c) => [c.time, c]));
    const latestCandle = candles[candles.length - 1];
    const showDay = (c: ResearchChartCandle) => {
      if (!legendRef.current) return;
      legendRef.current.textContent =
        `${dayLabel(c.time)} · O ${inr(c.open)} · H ${inr(c.high)} · L ${inr(c.low)} · C ${inr(c.close)}`;
    };
    const onCrosshairMove = (param: MouseEventParams<Time>) => {
      const key = timeKey(param.time);
      showDay((key && byDay.get(key)) || latestCandle);
    };
    showDay(latestCandle);
    chart.subscribeCrosshairMove(onCrosshairMove);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.remove();
    };
  }, [candles, theme]);

  // ── Caption price ──────────────────────────────────────────────────────────
  const latest = candles?.[candles.length - 1] ?? null;
  const quoteDay = istDay(quote?.asOf);
  const useQuote = !!quote && (!latest || !quoteDay || quoteDay >= latest.time);
  const shownPrice = useQuote ? quote.price : (latest?.close ?? null);
  // Day change is measured against the last close before the shown price's session.
  const sessionDay = useQuote ? quoteDay : (latest?.time ?? null);
  const previousClose =
    candles && sessionDay ? ([...candles].reverse().find((c) => c.time < sessionDay)?.close ?? null) : null;
  const dayChange = shownPrice != null && previousClose ? (shownPrice / previousClose - 1) * 100 : null;
  const priceNote = useQuote
    ? [quoteSourceLabel(quote.source), istDateTime(quote.asOf)].filter(Boolean).join(" · ")
    : latest
      ? `Close · ${dayLabel(latest.time)}`
      : null;

  const placeholder =
    state.status === "loading"
      ? "Loading NSE chart…"
      : state.status === "empty"
        ? `No NSE price history is available for ${symbol} yet.`
        : "The NSE chart couldn't load right now.";

  return (
    <figure style={{ margin: 0, display: "grid", gap: 10, minWidth: 0 }}>
      <figcaption
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "6px 12px",
        }}
      >
        <span style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "2px 10px" }}>
          <strong style={{ fontSize: "0.95rem", color: "var(--text-primary)" }}>NSE: {symbol}</strong>
          {shownPrice != null && (
            <span
              data-testid="chart-price"
              style={{ fontSize: "0.92rem", fontWeight: 750, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}
            >
              {inr(shownPrice)}
              {dayChange != null && (
                <span
                  style={{
                    marginLeft: 6,
                    fontSize: "0.8rem",
                    fontWeight: 700,
                    color: dayChange >= 0 ? "var(--tone-good-fg)" : "var(--tone-weak-fg)",
                  }}
                >
                  {dayChange >= 0 ? "+" : "−"}
                  {Math.abs(dayChange).toFixed(2)}%
                </span>
              )}
            </span>
          )}
          {priceNote && (
            <span style={{ fontSize: "0.76rem", color: "var(--text-secondary)" }}>{priceNote}</span>
          )}
        </span>
        <a
          href={tradingViewChartUrl(symbol, "D")}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 10px",
            borderRadius: 8,
            border: "1px solid var(--border-interactive)",
            color: "var(--text-secondary)",
            fontSize: "0.78rem",
            fontWeight: 650,
            textDecoration: "none",
          }}
        >
          Open in TradingView <ExternalLink size={13} aria-hidden />
        </a>
      </figcaption>

      <div className="nse-chart-host">
        {candles ? (
          <div
            ref={hostRef}
            role="img"
            aria-label={`${symbol} daily candlestick chart on the NSE`}
            style={{ position: "absolute", inset: 0 }}
          />
        ) : (
          <div
            role="status"
            style={{
              position: "absolute",
              inset: 0,
              display: "grid",
              placeItems: "center",
              padding: 16,
              textAlign: "center",
              borderRadius: 10,
              border: "1px dashed var(--border-interactive)",
              color: "var(--text-secondary)",
              fontSize: "0.85rem",
            }}
          >
            {placeholder}
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          gap: "2px 12px",
          fontSize: "0.74rem",
          color: "var(--text-secondary)",
        }}
      >
        <span ref={legendRef} style={{ fontVariantNumeric: "tabular-nums" }} />
        <span style={{ color: "var(--text-dim)" }}>Daily NSE candles · last 6 months</span>
      </div>
    </figure>
  );
}
