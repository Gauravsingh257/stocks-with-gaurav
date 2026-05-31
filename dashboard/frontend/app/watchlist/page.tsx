"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  Bookmark,
  Trash2,
  ExternalLink,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Minus,
  ArrowUpRight,
  ArrowDownRight,
  Activity,
} from "lucide-react";
import { api, type WatchlistIntelItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import WatchlistMonitor from "./WatchlistMonitor";
import { useMergedMarketLtp } from "@/lib/realtimeRegistry";
import { subscribeWatchlistOsHint } from "@/lib/watchlistOsBridge";

type Position = {
  id: number;
  symbol: string;
  entry_price: number;
  stop_loss: number | null;
  target_1: number | null;
  target_2: number | null;
  holding_period: string | null;
  taken_at: string;
  status: string;
  live_status: string | null;
  cmp: number | null;
  cmp_source: string | null;
  pnl_pct: number | null;
  pnl_r: number | null;
  holding_days: number | null;
  source?: string | null;
};

const SOURCE_LABEL: Record<string, { label: string; color: string }> = {
  CMP_BUY:           { label: "Buy@CMP",  color: "#34d399" },
  WATCHLIST_TRIGGER: { label: "Triggered", color: "#f472b6" },
  RESEARCH_AUTO:     { label: "Research", color: "#7dd3fc" },
  MANUAL:            { label: "Manual",   color: "#a3a3a3" },
};

function stripNse(s: string): string {
  return s.replace(/^NSE:/i, "").trim().toUpperCase();
}

// Institutional-terminal feel: monospaced tabular numerals for every price.
// Matches the aesthetic of Bloomberg/Zerodha/TradingView — numbers line up
// vertically across rows so the eye scans P&L without re-anchoring.
const MONO_NUMS: React.CSSProperties = {
  fontFamily: "ui-monospace, 'JetBrains Mono', 'SF Mono', 'Geist Mono', Menlo, monospace",
  fontVariantNumeric: "tabular-nums slashed-zero",
  fontFeatureSettings: '"tnum", "zero"',
};

function fmtPrice(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `₹${n.toFixed(2)}`;
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

// State badge: border-only, no background fill. Reads as a classification
// label rather than an action button — matches the institutional aesthetic
// where status labels are restrained and the data does the talking.
const STATE_STYLE: Record<string, { border: string; text: string }> = {
  "GOOD ENTRY":    { border: "rgba(0,224,150,0.55)",  text: "#00e096" },
  WATCH:           { border: "rgba(148,163,184,0.45)", text: "#94a3b8" },
  "BREAKOUT SOON": { border: "rgba(245,158,11,0.55)", text: "#ffa502" },
  WEAK:            { border: "rgba(255,71,87,0.5)",   text: "#ff4757" },
  AVOID:           { border: "rgba(255,71,87,0.65)",  text: "#ff5e7e" },
  ACTIVE:          { border: "rgba(0,224,150,0.45)",  text: "#00e096" },
};

function StateBadge({ state }: { state: string }) {
  const s = STATE_STYLE[state] || { border: "rgba(255,255,255,0.18)", text: "var(--text-secondary)" };
  return (
    <span
      style={{
        padding: "2px 8px",
        borderRadius: 3,
        background: "transparent",
        border: `1px solid ${s.border}`,
        color: s.text,
        fontSize: "0.6rem",
        fontWeight: 700,
        letterSpacing: 0.8,
        textTransform: "uppercase",
        lineHeight: 1.4,
      }}
    >
      {state}
    </span>
  );
}

function TrendIcon({ trend }: { trend: string | undefined }) {
  if (trend === "bullish") return <TrendingUp size={13} color="#00e096" />;
  if (trend === "bearish") return <TrendingDown size={13} color="#ff4757" />;
  return <Minus size={13} color="var(--text-dim)" />;
}

function StockCard({
  row,
  cmp,
  onRemove,
  onTakeEntry,
  takingEntry,
  hasActivePosition,
}: {
  row: WatchlistIntelItem;
  cmp: number | null;
  onRemove: (sym: string) => void;
  onTakeEntry: (row: WatchlistIntelItem, cmp: number | null) => void;
  takingEntry: boolean;
  hasActivePosition: boolean;
}) {
  const state = row.monitor_state || "WATCH";
  const sentence = row.smart_sentence || "Monitoring setup.";
  const entry = row.recommendation?.entry ?? null;
  const sl = row.recommendation?.stop_loss ?? null;
  const target = row.recommendation?.target ?? null;
  const showLevels = Boolean(row.recommendation?.show_trade_levels) && state === "GOOD ENTRY" && entry && sl;
  const rr = row.risk?.rr ?? null;

  const pctChange =
    cmp != null && entry != null && entry > 0
      ? ((cmp - entry) / entry) * 100
      : null;

  return (
    <div
      className="wl-card"
      style={{
        padding: "14px 16px",
        background: "rgba(255,255,255,0.015)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderLeft: state === "GOOD ENTRY"
          ? "2px solid rgba(0,224,150,0.55)"
          : state === "AVOID" || state === "WEAK"
            ? "2px solid rgba(255,71,87,0.45)"
            : state === "BREAKOUT SOON"
              ? "2px solid rgba(245,158,11,0.45)"
              : "1px solid rgba(255,255,255,0.06)",
        borderRadius: 6,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Top row: symbol + CMP + remove */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <a
              href={`https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(row.symbol)}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: "var(--text-primary)",
                fontWeight: 700,
                fontSize: "1rem",
                letterSpacing: 0.4,
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              {row.symbol} <ExternalLink size={10} color="var(--text-dim)" />
            </a>
            <StateBadge state={state} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.68rem", color: "var(--text-dim)", letterSpacing: 0.2 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <TrendIcon trend={row.trend_state} />
              {row.trend_state || "—"}
            </span>
            {row.horizon && <span>· {row.horizon}</span>}
            {row.intelligence?.risk_grade && (
              <span>· Grade {row.intelligence.risk_grade}</span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--text-primary)", ...MONO_NUMS }}>
              {cmp != null ? `₹${cmp.toFixed(2)}` : "—"}
            </div>
            {pctChange != null && (
              <div
                style={{
                  fontSize: "0.66rem",
                  color: pctChange >= 0 ? "#00e096" : "#ff4757",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 2,
                  ...MONO_NUMS,
                }}
              >
                {pctChange >= 0 ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                {pctChange >= 0 ? "+" : ""}
                {pctChange.toFixed(2)}% vs entry
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={() => onRemove(row.symbol)}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)", padding: 2 }}
            title="Remove"
            aria-label={`Remove ${row.symbol}`}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {/* Smart sentence — restrained, single source of editorial truth */}
      <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", lineHeight: 1.5, letterSpacing: 0.05 }}>
        {sentence}
      </div>

      {/* Actionable levels — institutional row: thin top divider, mono nums,
          restrained accents. Reads like a Bloomberg ticket. */}
      {showLevels && entry && sl && (
        <div
          style={{
            paddingTop: 10,
            borderTop: "1px dashed rgba(0,224,150,0.22)",
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr) auto",
            gap: 12,
            alignItems: "end",
            fontSize: "0.78rem",
            ...MONO_NUMS,
          }}
        >
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.9, marginBottom: 2 }}>Entry</div>
            <strong style={{ color: "var(--text-primary)", fontWeight: 600 }}>{fmtPrice(entry)}</strong>
          </div>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.9, marginBottom: 2 }}>SL</div>
            <strong style={{ color: "#ff4757", fontWeight: 600 }}>{fmtPrice(sl)}</strong>
          </div>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.9, marginBottom: 2 }}>Target</div>
            <strong style={{ color: "#00e096", fontWeight: 600 }}>{fmtPrice(target)}</strong>
          </div>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.9, marginBottom: 2 }}>R:R</div>
            <strong style={{ color: "var(--text-primary)", fontWeight: 600 }}>{rr ? `1:${rr.toFixed(2)}` : "—"}</strong>
          </div>
          {hasActivePosition ? (
            <button
              type="button"
              disabled
              style={{
                padding: "8px 16px",
                background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.1)",
                color: "var(--text-dim)",
                borderRadius: 6,
                fontSize: "0.72rem",
                fontWeight: 700,
                cursor: "not-allowed",
              }}
            >
              In position
            </button>
          ) : (
            <button
              type="button"
              onClick={() => onTakeEntry(row, cmp)}
              disabled={takingEntry}
              style={{
                padding: "8px 16px",
                background: takingEntry ? "rgba(0,224,150,0.2)" : "#00e096",
                border: "1px solid rgba(0,224,150,0.7)",
                color: takingEntry ? "var(--text-primary)" : "#0a0e1a",
                borderRadius: 6,
                fontSize: "0.72rem",
                fontWeight: 800,
                letterSpacing: 0.3,
                cursor: takingEntry ? "wait" : "pointer",
              }}
            >
              {takingEntry ? "…" : "Take Entry"}
            </button>
          )}
        </div>
      )}

      {/* Non-actionable fallback */}
      {!showLevels && (
        <div style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>
          No safe entry currently —{" "}
          <span style={{ color: "var(--text-secondary)" }}>
            {row.intelligence?.next_trigger || row.recommendation?.monitoring_message || "waiting for confirmation"}
          </span>
        </div>
      )}
    </div>
  );
}

function PositionRow({
  pos,
  cmpLive,
  onClose,
}: {
  pos: Position;
  cmpLive: number | null;
  onClose: (id: number) => void;
}) {
  const cmp = cmpLive ?? pos.cmp;
  const entry = pos.entry_price;
  const pnlPct = cmp != null && entry > 0 ? ((cmp - entry) / entry) * 100 : pos.pnl_pct;
  const pnlR = cmp != null && pos.stop_loss != null && entry > 0
    ? (cmp - entry) / Math.abs(entry - pos.stop_loss)
    : pos.pnl_r;
  const status = pos.live_status || pos.status;
  const statusColor =
    status === "Near Target" || status === "Running"
      ? "#00e096"
      : status === "SL Risk" || status === "Underwater"
        ? "#ff4757"
        : "var(--text-secondary)";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1.2fr 0.8fr 0.8fr 0.7fr 0.7fr 0.7fr 0.7fr auto",
        gap: 10,
        alignItems: "center",
        padding: "10px 14px",
        background: "rgba(255,255,255,0.015)",
        border: "1px solid rgba(255,255,255,0.05)",
        borderLeft: "2px solid rgba(0,224,150,0.35)",
        borderRadius: 4,
        fontSize: "0.72rem",
        ...MONO_NUMS,
      }}
    >
      <div style={{ fontFamily: "inherit" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <strong style={{ color: "var(--text-primary)", letterSpacing: 0.3, fontFamily: "var(--font-sans, system-ui)" }}>{pos.symbol}</strong>
          {pos.source && SOURCE_LABEL[pos.source] && (
            <span style={{ fontSize: "0.5rem", fontWeight: 800, padding: "1px 5px", borderRadius: 4, textTransform: "uppercase", letterSpacing: 0.5, color: SOURCE_LABEL[pos.source].color, border: `1px solid ${SOURCE_LABEL[pos.source].color}55`, fontFamily: "var(--font-sans, system-ui)" }}>
              {SOURCE_LABEL[pos.source].label}
            </span>
          )}
        </span>
        <div style={{ fontSize: "0.58rem", color: "var(--text-dim)", letterSpacing: 0.2, fontFamily: "var(--font-sans, system-ui)" }}>
          {pos.holding_period || "Swing"} · {pos.holding_days ?? 0}d
        </div>
      </div>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.8, fontFamily: "var(--font-sans, system-ui)" }}>Entry</div>
        {fmtPrice(entry)}
      </div>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.8, fontFamily: "var(--font-sans, system-ui)" }}>CMP</div>
        <strong>{fmtPrice(cmp)}</strong>
      </div>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.8, fontFamily: "var(--font-sans, system-ui)" }}>SL</div>
        <span style={{ color: "#ff4757" }}>{fmtPrice(pos.stop_loss)}</span>
      </div>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.8, fontFamily: "var(--font-sans, system-ui)" }}>Target</div>
        <span style={{ color: "#00e096" }}>{fmtPrice(pos.target_2 ?? pos.target_1)}</span>
      </div>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.8, fontFamily: "var(--font-sans, system-ui)" }}>P&amp;L</div>
        <strong style={{ color: (pnlPct ?? 0) >= 0 ? "#00e096" : "#ff4757" }}>{fmtPct(pnlPct)}</strong>
      </div>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.56rem", textTransform: "uppercase", letterSpacing: 0.8, fontFamily: "var(--font-sans, system-ui)" }}>R</div>
        <span style={{ color: (pnlR ?? 0) >= 0 ? "#00e096" : "#ff4757" }}>{pnlR != null ? pnlR.toFixed(2) : "—"}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <span style={{ fontSize: "0.62rem", color: statusColor, fontWeight: 700, letterSpacing: 0.3 }}>
          {status}
        </span>
        <button
          type="button"
          onClick={() => onClose(pos.id)}
          style={{
            padding: "3px 8px",
            background: "transparent",
            border: "1px solid rgba(255,255,255,0.12)",
            color: "var(--text-secondary)",
            borderRadius: 4,
            fontSize: "0.62rem",
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </div>
    </div>
  );
}

export default function WatchlistPage() {
  const { user, token } = useAuth();
  const [intel, setIntel] = useState<WatchlistIntelItem[]>([]);
  const [savedSymbols, setSavedSymbols] = useState<{ symbol: string }[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [takingEntry, setTakingEntry] = useState<string | null>(null);

  const marketLtp = useMergedMarketLtp();

  const loadAll = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [intelData, wl, pos] = await Promise.all([
        api.getWatchlistOperating(token),
        api.getWatchlist(token),
        api.listPositions(token, "ACTIVE"),
      ]);
      setIntel(intelData.items || []);
      setSavedSymbols(wl.items || []);
      setPositions(pos.items || []);
    } catch {
      setError("Could not load watchlist.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    const uid = user?.id;
    if (uid == null) return;
    let deb: ReturnType<typeof setTimeout> | undefined;
    const unsub = subscribeWatchlistOsHint(({ userId }) => {
      if (userId !== uid) return;
      if (deb) clearTimeout(deb);
      deb = setTimeout(loadAll, 450);
    });
    return () => {
      unsub();
      if (deb) clearTimeout(deb);
    };
  }, [user?.id, loadAll]);

  const handleRemove = async (symbol: string) => {
    if (!token) return;
    const prevIntel = intel;
    const prevSaved = savedSymbols;
    setIntel((p) => p.filter((x) => x.symbol !== symbol));
    setSavedSymbols((p) => p.filter((x) => x.symbol !== symbol));
    try {
      await api.removeFromWatchlist(token, symbol);
      await loadAll();
    } catch {
      setIntel(prevIntel);
      setSavedSymbols(prevSaved);
      setError("Could not remove — restored.");
    }
  };

  const handleTakeEntry = async (row: WatchlistIntelItem, cmp: number | null) => {
    if (!token) return;
    const entry = row.recommendation?.entry ?? cmp;
    const sl = row.recommendation?.stop_loss ?? null;
    const target = row.recommendation?.target ?? null;
    if (!entry) {
      setError("Cannot take entry: no entry price available.");
      return;
    }
    setTakingEntry(row.symbol);
    try {
      await api.takeEntry(token, {
        symbol: row.symbol,
        entry_price: entry,
        stop_loss: sl,
        target_1: target,
        target_2: null,
        holding_period: row.horizon === "LONGTERM" ? "Long-term" : "Swing",
      });
      await loadAll();
    } catch (e) {
      setError(`Could not open position: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTakingEntry(null);
    }
  };

  const handleClosePosition = async (id: number) => {
    if (!token) return;
    try {
      await api.closePosition(token, id);
      await loadAll();
    } catch {
      setError("Could not close position.");
    }
  };

  if (!user) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <Bookmark size={40} color="var(--text-dim)" style={{ margin: "0 auto 16px" }} />
        <h2 style={{ fontSize: "1.2rem", marginBottom: 8 }}>Sign in to use Watchlist</h2>
        <p style={{ color: "var(--text-secondary)", marginBottom: 20 }}>
          Save stocks, monitor them live, and track positions you take.
        </p>
        <Link href="/login" className="btn-accent" style={{ textDecoration: "none", padding: "10px 24px" }}>
          Sign In
        </Link>
      </div>
    );
  }

  const activePositionSymbols = new Set(positions.map((p) => stripNse(p.symbol)));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 1100, margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          paddingBottom: 6,
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 800 }}>Watchlist</h1>
          <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            <strong style={{ color: "var(--text-primary)" }}>{savedSymbols.length}</strong> watching
            {positions.length > 0 && (
              <>
                {" · "}
                <strong style={{ color: "#00e096" }}>{positions.length}</strong> active position
                {positions.length === 1 ? "" : "s"}
              </>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={loadAll}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            background: "transparent",
            border: "1px solid rgba(255,255,255,0.1)",
            color: "var(--text-secondary)",
            borderRadius: 6,
            fontSize: "0.74rem",
            cursor: "pointer",
          }}
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 12px",
            background: "rgba(255,71,87,0.08)",
            border: "1px solid rgba(255,71,87,0.3)",
            borderRadius: 8,
            color: "#ff5e7e",
            fontSize: "0.78rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Active Watchlist Monitor — entry-trigger lifecycle (new) */}
      <WatchlistMonitor />

      {/* Active Positions */}
      {positions.length > 0 && (
        <section>
          <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.5, marginBottom: 8, display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Activity size={11} color="#00e096" /> ACTIVE POSITIONS
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {positions.map((p) => {
              const live = marketLtp[stripNse(p.symbol)] ?? null;
              return <PositionRow key={p.id} pos={p} cmpLive={live} onClose={handleClosePosition} />;
            })}
          </div>
        </section>
      )}

      {/* Legacy symbol-only watches (deprecated). The Active Watchlist above is
          the primary flow; this only renders pre-existing symbol-only entries so
          nothing silently disappears. Re-add from Research for full tracking. */}
      {intel.length > 0 && (
        <section>
          <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.5, marginBottom: 6 }}>
            LEGACY WATCHES · SYMBOL-ONLY ({intel.length})
          </div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", marginBottom: 8 }}>
            These were saved without a setup. Re-add from{" "}
            <Link href="/research" style={{ color: "var(--accent)" }}>Research</Link>{" "}
            to track entry zone, SL and targets in the Active Watchlist above.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {intel.map((row, i) => {
              const cmp = marketLtp[stripNse(row.symbol)] ?? row.quote_ltp ?? null;
              return (
                <div key={row.symbol} className="wl-card-enter" style={{ animationDelay: `${Math.min(i, 14) * 28}ms` }}>
                  <StockCard
                    row={row}
                    cmp={cmp}
                    onRemove={handleRemove}
                    onTakeEntry={handleTakeEntry}
                    takingEntry={takingEntry === row.symbol}
                    hasActivePosition={activePositionSymbols.has(stripNse(row.symbol))}
                  />
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Subtle staggered fade-in on initial mount — the one "high-impact
          moment" the frontend-design skill calls for. Restrained per the
          institutional aesthetic; no spring physics, no scale, no rotation. */}
      <style jsx global>{`
        @keyframes wlCardEnter {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .wl-card-enter {
          animation: wlCardEnter 360ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
        }
        @media (prefers-reduced-motion: reduce) {
          .wl-card-enter { animation: none; }
        }
      `}</style>
    </div>
  );
}
