"use client";
/**
 * /charts — SMC Charts Page (Phase 4 live)
 * Real OHLC from /api/ohlc/{symbol}?interval={interval}
 * Zone overlays from /api/zones/{symbol}
 * SL / TP / Entry lines from active trades
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  TrendingUp, RefreshCw, AlertCircle, Wifi, WifiOff,
  Layers, ChevronDown,
} from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

// ── Types ────────────────────────────────────────────────────────────────────
interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number }
interface ZoneItem { direction: string; top: number; bottom: number; zone_type: string; strength: number; tapped: boolean }
interface PriceLine { type: string; price: number; label: string; color: string }
interface ZonesData { symbol: string; zones: ZoneItem[]; active_lines: PriceLine[]; engine_live: boolean }

const INTERVALS = ["1m", "5m", "15m", "1h", "1D"] as const;
type Interval = typeof INTERVALS[number];

const ZONE_COLORS: Record<string, { line: string }> = {
  "LONG":  { line: "#00d4ff" },
  "SHORT": { line: "#ff4757" },
  "BULL":  { line: "#00e096" },
  "BEAR":  { line: "#ffa502" },
};

export default function ChartsPage() {
  const [symbols,    setSymbols]   = useState<string[]>(["NIFTY 50", "NIFTY BANK"]);
  const [selected,   setSelected]  = useState("NIFTY 50");
  const [interval,   setIntvl]     = useState<Interval>("15m");
  const [candles,    setCandles]   = useState<Candle[] | null>(null);
  const [zonesData,  setZones]     = useState<ZonesData | null>(null);
  const [loading,    setLoading]   = useState(true);
  const [error,      setError]     = useState<string | null>(null);
  const [kiteOk,     setKiteOk]    = useState<boolean | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<unknown>(null);

  // ── Load symbol list ───────────────────────────────────────────────────────
  useEffect(() => {
    fetch(`${BASE}/api/chart-symbols`)
      .then(r => r.json())
      .then(d => { if (Array.isArray(d?.symbols) && d.symbols.length) setSymbols(d.symbols); })
      .catch(() => {});
  }, []);

  // ── Fetch OHLC + Zones ─────────────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const encSym = encodeURIComponent(selected);
    try {
      const [ohlcRes, zonesRes] = await Promise.all([
        fetch(`${BASE}/api/ohlc/${encSym}?interval=${interval}`),
        fetch(`${BASE}/api/zones/${encSym}`),
      ]);
      if (!ohlcRes.ok) {
        const detail = await ohlcRes.json().catch(() => ({}));
        setKiteOk(false);
        setError((detail as { detail?: string })?.detail || `OHLC fetch failed (${ohlcRes.status})`);
        setCandles([]);
        setLoading(false);
        return;
      }
      const ohlcData  = await ohlcRes.json();
      const zonesJson = zonesRes.ok ? await zonesRes.json() : null;
      setCandles(ohlcData.candles ?? []);
      setZones(zonesJson);
      setKiteOk(true);
      setLastRefresh(new Date());
    } catch {
      setError("Backend unreachable — is the dashboard server running?");
      setKiteOk(false);
      setCandles([]);
    } finally {
      setLoading(false);
    }
  }, [selected, interval]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Refresh chart: 10s for 1m/5m/15m (real-time tail), 30s for 1h/1D
  const refreshMs = ["1m", "5m", "15m"].includes(interval) ? 10_000 : 30_000;
  useEffect(() => {
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") fetchData();
    }, refreshMs);
    return () => clearInterval(id);
  }, [fetchData, refreshMs]);

  // ── Build chart ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (candles === null) return;
    let destroyed = false;
    let cleanup: (() => void) | undefined;

    async function buildChart() {
      if (!containerRef.current || destroyed) return;
      try {
        const { createChart, ColorType, CandlestickSeries } = await import("lightweight-charts");
        if (destroyed || !containerRef.current) return;

        if (chartRef.current) (chartRef.current as { remove(): void }).remove();

        const el = containerRef.current;
        const chart = createChart(el, {
          layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8899bb" },
          grid:   { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
          crosshair: { mode: 1 },
          rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
          timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
          width:  el.clientWidth,
          height: el.clientHeight,
        });

        const series = chart.addSeries(CandlestickSeries, {
          upColor:         "#00e096", downColor:       "#ff4757",
          borderUpColor:   "#00e096", borderDownColor: "#ff4757",
          wickUpColor:     "#00e096", wickDownColor:   "#ff4757",
        });

        const data = candles && candles.length > 0 ? candles : _demoData();
        series.setData(data.map(c => ({ ...c, time: c.time as unknown as import("lightweight-charts").Time })));

        // Zone price lines
        if (zonesData?.zones) {
          for (const zone of zonesData.zones) {
            const col   = (ZONE_COLORS[zone.direction] ?? ZONE_COLORS["LONG"]).line;
            const color = col + (zone.tapped ? "66" : "cc");
            const label = `${zone.zone_type} ${zone.direction}${zone.tapped ? " ✓" : ""}`;
            if (zone.top    != null) series.createPriceLine({ price: zone.top,    color, lineWidth: 1, lineStyle: zone.tapped ? 2 : 0, axisLabelVisible: true,  title: `▲ ${label}` });
            if (zone.bottom != null) series.createPriceLine({ price: zone.bottom, color, lineWidth: 1, lineStyle: zone.tapped ? 2 : 0, axisLabelVisible: false, title: `▼ ${label}` });
          }
        }

        // Active trade lines
        if (zonesData?.active_lines) {
          for (const line of zonesData.active_lines) {
            series.createPriceLine({
              price: line.price, color: line.color,
              lineWidth: line.type === "entry" ? 2 : 1,
              lineStyle: line.type === "entry" ? 0 : 2,
              axisLabelVisible: true, title: line.label,
            });
          }
        }

        chartRef.current = chart;
        chart.timeScale().fitContent();

        const ro = new ResizeObserver(() => {
          if (containerRef.current) {
            const el = containerRef.current;
            chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
          }
        });
        ro.observe(el);
        cleanup = () => ro.disconnect();
      } catch (e) { console.warn("Chart init error", e); }
    }

    buildChart();
    return () => {
      destroyed = true;
      cleanup?.();
    };
  }, [candles, zonesData]);

  const activeZones = zonesData?.zones?.filter(z => !z.tapped) ?? [];
  const tappedZones = zonesData?.zones?.filter(z =>  z.tapped) ?? [];
  const activeLines = zonesData?.active_lines ?? [];

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 className="text-xl md:text-2xl lg:text-3xl font-bold m-0">SMC Charts</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
            Live OHLC · OB / FVG zone overlays · Active trade lines
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {kiteOk === true  && <span className="badge badge-live"    style={{ fontSize: "0.72rem" }}><Wifi    size={10}/> Kite Live</span>}
          {kiteOk === false && (
            <>
              <span className="badge badge-warning" style={{ fontSize: "0.72rem" }}><WifiOff size={10}/> Kite Offline</span>
              <button
                type="button"
                onClick={() => { window.location.href = "/api/kite/login"; }}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  padding: "6px 12px", fontSize: "0.75rem", fontWeight: 600,
                  color: "var(--accent)", border: "1px solid var(--accent)", borderRadius: 6,
                  cursor: "pointer", background: "transparent",
                }}
              >
                Connect Kite
              </button>
            </>
          )}
          <button className="btn-accent" onClick={fetchData} disabled={loading}
            style={{ fontSize: "0.78rem", padding: "6px 14px", opacity: loading ? 0.6 : 1 }}>
            <RefreshCw size={12} style={{ display: "inline", marginRight: 5,
              animation: loading ? "spin 1s linear infinite" : "none" }} />
            Refresh
          </button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="glass" style={{ padding: "12px 16px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ position: "relative" }}>
          <select className="input-dark" value={selected} onChange={e => setSelected(e.target.value)}
            style={{ paddingRight: 28, width: 165, appearance: "none" }}>
            {symbols.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <ChevronDown size={12} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", color: "var(--text-secondary)", pointerEvents: "none" }} />
        </div>
        <div style={{ display: "flex", gap: 5 }}>
          {INTERVALS.map(iv => (
            <button key={iv} onClick={() => setIntvl(iv)} style={{
              padding: "5px 13px", borderRadius: 6, fontSize: "0.78rem", cursor: "pointer",
              background: interval === iv ? "var(--accent-dim)" : "rgba(255,255,255,0.05)",
              border:     interval === iv ? "1px solid var(--accent)" : "1px solid var(--border)",
              color:      interval === iv ? "var(--accent)" : "var(--text-secondary)",
            }}>{iv}</button>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 14, fontSize: "0.72rem", color: "var(--text-secondary)" }}>
          {candles && candles.length > 0 && <span>{candles.length} bars</span>}
          {activeZones.length > 0 && <span style={{ color: "var(--accent)" }}><Layers size={10} style={{ display: "inline", marginRight: 3 }} />{activeZones.length} zones</span>}
          {lastRefresh && <span>Updated {lastRefresh.toLocaleTimeString("en-IN")}</span>}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="glass" style={{ padding: "12px 18px", color: "var(--warning)", fontSize: "0.82rem", display: "flex", alignItems: "center", gap: 10, borderLeft: "3px solid var(--warning)" }}>
          <AlertCircle size={15} />
          <div>
            <strong>Chart data unavailable</strong> — {error}
            <div style={{ fontSize: "0.73rem", color: "var(--text-secondary)", marginTop: 3 }}>
              Showing demo data. Click Connect Kite above or open /api/kite/login to log in to Zerodha.
            </div>
          </div>
        </div>
      )}

      {/* Chart */}
      <div className="glass w-full overflow-hidden p-0 border-cyan-500/10 shadow-[0_0_10px_rgba(0,255,255,0.08)]">
        <div className="border-b border-cyan-500/10 flex items-center gap-2.5" style={{ padding: "14px 20px" }}>
          <TrendingUp size={16} color="var(--accent)" />
          <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{selected} · {interval}</span>
          {kiteOk === false && <span className="badge badge-paper" style={{ marginLeft: 6, fontSize: "0.68rem" }}>DEMO</span>}
          {kiteOk === true  && <span className="badge badge-live"  style={{ marginLeft: 6, fontSize: "0.68rem" }}>LIVE</span>}
          {loading && <span style={{ marginLeft: "auto", fontSize: "0.72rem", color: "var(--text-secondary)" }}>Loading…</span>}
        </div>
        <div ref={containerRef} className="w-full h-[300px] md:h-[400px] lg:h-[500px]" />
      </div>

      {/* Zone + Line panels */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="glass" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.08em", marginBottom: 10 }}>
            ACTIVE ZONES ({activeZones.length})
          </div>
          {activeZones.length === 0 ? (
            <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
              {zonesData?.engine_live ? "No active zones" : "Engine offline — no zone data"}
            </div>
          ) : activeZones.map((z, i) => {
            const c = (ZONE_COLORS[z.direction] ?? ZONE_COLORS["LONG"]).line;
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "0.78rem", marginBottom: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ width: 10, height: 10, borderRadius: 2, background: c + "55", border: `1px solid ${c}` }} />
                  <span style={{ fontWeight: 600 }}>{z.zone_type} {z.direction}</span>
                </div>
                <span style={{ color: "var(--text-secondary)" }}>
                  {z.bottom != null && z.top != null ? `${z.bottom.toLocaleString()} – ${z.top.toLocaleString()}` : (z.top ?? "—").toLocaleString()}
                </span>
              </div>
            );
          })}
          {tappedZones.length > 0 && (
            <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)", marginTop: 4 }}>
              + {tappedZones.length} tapped (dashed lines)
            </div>
          )}
        </div>

        <div className="glass" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.08em", marginBottom: 10 }}>
            TRADE LEVELS ({activeLines.length})
          </div>
          {activeLines.length === 0 ? (
            <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>No active trades for {selected}</div>
          ) : activeLines.map((l, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "0.78rem", marginBottom: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ width: 22, height: 2, background: l.color, borderRadius: 1 }} />
                <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>{l.type}</span>
              </div>
              <span style={{ color: l.color, fontWeight: 600 }}>{l.price.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="glass" style={{ padding: "12px 20px" }}>
        <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.08em", marginBottom: 8 }}>OVERLAY KEY</div>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
          {[
            { label: "OB Long",  color: "#00d4ffcc" },
            { label: "OB Short", color: "#ff4757cc" },
            { label: "Tapped",   color: "#ffffff33", dashed: true },
            { label: "SL",       color: "#ff4757" },
            { label: "Target",   color: "#00e096" },
            { label: "Entry",    color: "#00d4ff" },
          ].map(({ label, color, dashed }) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.75rem", color: "var(--text-secondary)" }}>
              <div style={{ width: 24, height: 2, background: dashed ? "transparent" : color, borderBottom: dashed ? `2px dashed ${color}` : "none", borderRadius: 1 }} />
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function _demoData(): Candle[] {
  const now  = Math.floor(Date.now() / 1000);
  return Array.from({ length: 80 }, (_, i) => {
    const base  = 22400 + Math.sin(i * 0.25) * 380 + i * 1.5;
    const open  = base + (Math.random() - 0.5) * 40;
    const close = open + (Math.random() - 0.47) * 60;
    return {
      time: now - (80 - i) * 900,
      open, close,
      high: Math.max(open, close) + Math.random() * 35,
      low:  Math.min(open, close) - Math.random() * 35,
      volume: Math.floor(Math.random() * 50000),
    };
  });
}
