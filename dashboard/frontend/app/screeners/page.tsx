"use client";
/**
 * /screeners — Scanner suite (multi-scanner).
 *
 * Pure read of pre-computed Redis snapshots (served by /api/screeners). The page
 * never triggers a scan, so it stays instant under any load. Logged-in users see
 * full ranked rows; anonymous users see a locked teaser (counts + tiers).
 *
 * Scanners:
 *   1. supertrend_flip  — Supertrend(10,3) red→green flip above 10 EMA
 *   2. sector_rotation  — strong stock in a sector leading NIFTY, with news catalyst
 */
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Radar, Lock, Download, RefreshCw, TrendingUp, AlertTriangle, LogIn, Layers, Newspaper } from "lucide-react";
import { api, ScreenerCatalogItem, ScreenerResult, ScreenerRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// Scanner presentation metadata (keyed by stable scanner name).
const SCANNER_META: Record<string, { label: string; blurb: string }> = {
  supertrend_flip: {
    label: "Supertrend Flip",
    blurb: "Supertrend(10,3) red→green flip, above 10 EMA — ranked by confluence quality",
  },
  sector_rotation: {
    label: "Sector Rotation",
    blurb: "Strong stocks inside sectors outperforming the market (live constituent momentum) + news catalyst",
  },
};
const DEFAULT_SCANNERS = ["supertrend_flip", "sector_rotation"];

const TIMEFRAMES = [
  { tf: "1D", label: "Daily" },
  { tf: "1W", label: "Weekly" },
];

const TIER_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  quality:     { bg: "rgba(0,209,140,0.15)",  fg: "#00d18c", label: "Quality" },
  momentum:    { bg: "rgba(0,212,255,0.15)",  fg: "#22d3ee", label: "Momentum" },
  speculative: { bg: "rgba(255,170,0,0.15)",  fg: "#ffaa00", label: "Speculative" },
};

function fmt(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtSigned(v: number | null | undefined, d = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(d)}`;
}

function fmtVol(v: number): string {
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
}

// Tone → colour (GDELT tone: >0 positive, <0 negative).
function toneColor(t: number | null | undefined): string {
  if (t === null || t === undefined) return "#94a3b8";
  if (t >= 1) return "#00d18c";
  if (t <= -1) return "#ff4d4d";
  return "#ffaa00";
}

function TierBadge({ tier }: { tier: string }) {
  const s = TIER_STYLE[tier] || TIER_STYLE.speculative;
  return (
    <span className="px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ background: s.bg, color: s.fg }}>{s.label}</span>
  );
}

function toCsv(scanner: string, rows: ScreenerRow[]): string {
  if (scanner === "sector_rotation") {
    const head = ["Symbol", "Sector", "SectorRS%", "NewsTone", "NewsArticles", "Score", "Tier", "Close", "Stop", "1mMom%", "From52wHigh%", "AvgVol", "Headline"];
    const lines = rows.map((r) => [
      r.symbol, r.sector ?? "", r.sector_rel20_pct ?? "", r.news_tone ?? "", r.news_articles ?? "",
      r.quality_score, r.tier, r.close, r.stop, r.mom_long_pct, r.from_high_pct, r.avg_vol,
      `"${(r.news_headline ?? "").replace(/"/g, "'")}"`,
    ].join(","));
    return [head.join(","), ...lines].join("\n");
  }
  const head = ["Symbol", "Score", "Tier", "Flip", "Close", "Stop", "EMA10", "From52wHigh%", "RiskToStop%", "1mMom%", "AvgVol"];
  const lines = rows.map((r) => [
    r.symbol, r.quality_score, r.tier, r.flip, r.close, r.stop, r.ema10,
    r.from_high_pct, r.risk_to_stop_pct, r.mom_long_pct, r.avg_vol,
  ].join(","));
  return [head.join(","), ...lines].join("\n");
}

export default function ScreenersPage() {
  const { user, token } = useAuth();
  const entitled = !!user;

  const [scanner, setScanner] = useState("supertrend_flip");
  const [tf, setTf] = useState("1W");
  const [catalog, setCatalog] = useState<ScreenerCatalogItem[]>([]);
  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState<string>("all");

  useEffect(() => {
    api.screenersCatalog().then((c) => setCatalog(c.scanners)).catch(() => {});
  }, []);

  // Distinct scanner names available (from catalog, else the known defaults).
  const scannerNames = (() => {
    const fromCatalog = Array.from(new Set(catalog.map((c) => c.name)));
    const names = fromCatalog.length ? fromCatalog : DEFAULT_SCANNERS;
    // Keep a stable, known order; append any unknown extras.
    const ordered = DEFAULT_SCANNERS.filter((n) => names.includes(n));
    names.forEach((n) => { if (!ordered.includes(n)) ordered.push(n); });
    return ordered;
  })();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.screener(scanner, tf, token);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [scanner, tf, token]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setTierFilter("all"); }, [scanner]);

  const rows = result?.rows ?? [];
  const visibleRows = tierFilter === "all" ? rows : rows.filter((r) => r.tier === tierFilter);
  const isSector = scanner === "sector_rotation";
  const meta = SCANNER_META[scanner] ?? { label: scanner, blurb: "" };

  const exportCsv = () => {
    if (!rows.length) return;
    const blob = new Blob([toCsv(scanner, rows)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${scanner}_${tf}_${result?.as_of ?? "latest"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center"
          style={{ background: "rgba(0,212,255,0.1)", border: "1px solid rgba(0,212,255,0.2)" }}>
          <Radar size={18} style={{ color: "#22d3ee" }} />
        </div>
        <div>
          <h1 className="text-xl font-bold" style={{ color: "var(--text-primary, #e2e8f0)" }}>Screeners</h1>
          <p className="text-xs" style={{ color: "var(--text-muted, #94a3b8)" }}>{meta.blurb}</p>
        </div>
      </div>

      {/* Scanner selector */}
      <div className="flex gap-2 flex-wrap mt-4 mb-3">
        {scannerNames.map((name) => {
          const m = SCANNER_META[name] ?? { label: name };
          const active = scanner === name;
          const Icon = name === "sector_rotation" ? Layers : Radar;
          return (
            <button key={name} onClick={() => setScanner(name)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{
                background: active ? "rgba(0,212,255,0.15)" : "rgba(15,23,42,0.6)",
                color: active ? "#22d3ee" : "var(--text-muted, #94a3b8)",
                border: active ? "1px solid rgba(0,212,255,0.35)" : "1px solid transparent",
              }}>
              <Icon size={15} /> {m.label}
            </button>
          );
        })}
      </div>

      {/* Timeframe tabs + actions */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
        <div className="flex gap-1 p-1 rounded-lg" style={{ background: "rgba(15,23,42,0.6)" }}>
          {TIMEFRAMES.map((t) => {
            const cat = catalog.find((c) => c.name === scanner && c.timeframe === t.tf);
            const active = tf === t.tf;
            return (
              <button key={t.tf} onClick={() => setTf(t.tf)}
                className="px-4 py-1.5 rounded-md text-sm font-medium transition-colors"
                style={{
                  background: active ? "rgba(0,212,255,0.15)" : "transparent",
                  color: active ? "#22d3ee" : "var(--text-muted, #94a3b8)",
                }}>
                {t.label}
                {cat?.hits != null && (
                  <span className="ml-1.5 text-xs opacity-70">({cat.hits})</span>
                )}
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="p-2 rounded-md" title="Refresh"
            style={{ background: "rgba(15,23,42,0.6)", color: "var(--text-muted, #94a3b8)" }}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
          {entitled && rows.length > 0 && (
            <button onClick={exportCsv}
              className="flex items-center gap-1.5 px-3 py-2 rounded-md text-sm"
              style={{ background: "rgba(0,212,255,0.1)", color: "#22d3ee" }}>
              <Download size={14} /> CSV
            </button>
          )}
        </div>
      </div>

      {/* Status / as-of stamp */}
      {result && (
        <div className="flex items-center gap-3 flex-wrap text-xs mb-3" style={{ color: "var(--text-muted, #94a3b8)" }}>
          {result.as_of && <span>Confirmed at close · <strong>{result.as_of}</strong></span>}
          <span>· {result.hits} matches</span>
          {result.universe_size && <span>· scanned {result.universe_size} liquid stocks</span>}
          {result.snapshot_stale && (
            <span className="flex items-center gap-1" style={{ color: "#ffaa00" }}>
              <AlertTriangle size={12} /> last known good (refreshing)
            </span>
          )}
        </div>
      )}

      {/* Tier filter */}
      {entitled && rows.length > 0 && result?.tiers && (
        <div className="flex gap-2 mb-3 text-xs">
          {["all", "quality", "momentum", "speculative"].map((t) => {
            const count = t === "all" ? rows.length : (result.tiers as Record<string, number>)[t] ?? 0;
            const active = tierFilter === t;
            return (
              <button key={t} onClick={() => setTierFilter(t)}
                className="px-2.5 py-1 rounded-full font-medium capitalize"
                style={{
                  background: active ? "rgba(0,212,255,0.15)" : "rgba(15,23,42,0.6)",
                  color: active ? "#22d3ee" : "var(--text-muted, #94a3b8)",
                }}>
                {t} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Body */}
      {loading && !result && (
        <div className="py-16 text-center text-sm" style={{ color: "var(--text-muted, #94a3b8)" }}>Loading…</div>
      )}
      {error && (
        <div className="py-8 text-center text-sm" style={{ color: "#ff4d4d" }}>{error}</div>
      )}

      {result && !loading && result.pending && (
        <div className="py-16 text-center text-sm" style={{ color: "var(--text-muted, #94a3b8)" }}>
          First scan is being computed — check back shortly.
        </div>
      )}

      {result && result.locked && !result.pending && (
        <LockedTeaser result={result} />
      )}

      {/* Full table */}
      {result && !result.locked && !result.pending && (
        rows.length === 0 ? (
          <div className="py-16 text-center text-sm" style={{ color: "var(--text-muted, #94a3b8)" }}>
            {isSector
              ? "No leading sector has a qualifying stock right now. Check the other timeframe."
              : "No fresh flips in the liquid universe right now. Check the other timeframe."}
          </div>
        ) : isSector ? (
          <SectorTable rows={visibleRows} />
        ) : (
          <SupertrendTable rows={visibleRows} />
        )
      )}

      {/* Disclaimer */}
      <p className="mt-4 text-xs" style={{ color: "var(--text-muted, #64748b)" }}>
        <TrendingUp size={11} className="inline mr-1" />
        {isSector
          ? "Sector leadership is computed from live constituent-stock momentum vs the scanned market; news heat/tone is from GDELT (aggregated public news). A screen, not advice — confirm on the chart and size for the stop."
          : "Signals are a screen, not advice. A Supertrend flip is a probability edge — size for the stop and do your own analysis."}
      </p>
    </div>
  );
}

function SupertrendTable({ rows }: { rows: ScreenerRow[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border" style={{ borderColor: "var(--border, rgba(0,212,255,0.08))" }}>
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: "rgba(15,23,42,0.6)", color: "var(--text-muted, #94a3b8)" }}>
            <th className="text-left px-3 py-2 font-medium">#</th>
            <th className="text-left px-3 py-2 font-medium">Symbol</th>
            <th className="text-right px-3 py-2 font-medium">Score</th>
            <th className="text-left px-3 py-2 font-medium">Tier</th>
            <th className="text-left px-3 py-2 font-medium">Flip</th>
            <th className="text-right px-3 py-2 font-medium">Close</th>
            <th className="text-right px-3 py-2 font-medium">Stop</th>
            <th className="text-right px-3 py-2 font-medium">From High</th>
            <th className="text-right px-3 py-2 font-medium">Risk</th>
            <th className="text-right px-3 py-2 font-medium">Avg Vol</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.symbol} className="border-t" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
              <td className="px-3 py-2" style={{ color: "var(--text-muted, #94a3b8)" }}>{i + 1}</td>
              <td className="px-3 py-2 font-semibold" style={{ color: "var(--text-primary, #e2e8f0)" }}>{r.symbol}</td>
              <td className="px-3 py-2 text-right font-bold" style={{ color: "#22d3ee" }}>{fmt(r.quality_score, 1)}</td>
              <td className="px-3 py-2"><TierBadge tier={r.tier} /></td>
              <td className="px-3 py-2 text-xs" style={{ color: "var(--text-muted, #94a3b8)" }}>
                {r.flip === "this_bar" ? "Just flipped" : "1 bar ago"}
              </td>
              <td className="px-3 py-2 text-right">{fmt(r.close)}</td>
              <td className="px-3 py-2 text-right" style={{ color: "#ff4d4d" }}>{fmt(r.stop)}</td>
              <td className="px-3 py-2 text-right">{fmt(r.from_high_pct, 1)}%</td>
              <td className="px-3 py-2 text-right">{fmt(r.risk_to_stop_pct, 1)}%</td>
              <td className="px-3 py-2 text-right" style={{ color: "var(--text-muted, #94a3b8)" }}>{fmtVol(r.avg_vol)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SectorTable({ rows }: { rows: ScreenerRow[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border" style={{ borderColor: "var(--border, rgba(0,212,255,0.08))" }}>
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: "rgba(15,23,42,0.6)", color: "var(--text-muted, #94a3b8)" }}>
            <th className="text-left px-3 py-2 font-medium">#</th>
            <th className="text-left px-3 py-2 font-medium">Symbol</th>
            <th className="text-left px-3 py-2 font-medium">Sector</th>
            <th className="text-right px-3 py-2 font-medium" title="Sector relative strength vs the scanned market">Sector RS</th>
            <th className="text-left px-3 py-2 font-medium">News</th>
            <th className="text-right px-3 py-2 font-medium">Score</th>
            <th className="text-left px-3 py-2 font-medium">Tier</th>
            <th className="text-right px-3 py-2 font-medium">Close</th>
            <th className="text-right px-3 py-2 font-medium">1M Mom</th>
            <th className="text-right px-3 py-2 font-medium">From High</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.symbol} className="border-t align-top" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
              <td className="px-3 py-2" style={{ color: "var(--text-muted, #94a3b8)" }}>{i + 1}</td>
              <td className="px-3 py-2">
                <div className="font-semibold" style={{ color: "var(--text-primary, #e2e8f0)" }}>{r.symbol}</div>
                {r.news_headline && (
                  <a href={r.news_url ?? undefined} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1 mt-0.5 text-xs hover:underline"
                    style={{ color: "var(--text-muted, #94a3b8)", maxWidth: 260 }} title={r.news_headline}>
                    <Newspaper size={10} className="shrink-0" />
                    <span className="truncate">{r.news_headline}</span>
                  </a>
                )}
              </td>
              <td className="px-3 py-2">
                <span className="px-2 py-0.5 rounded-md text-xs font-medium"
                  style={{ background: "rgba(0,209,140,0.12)", color: "#00d18c" }}>{r.sector ?? "—"}</span>
              </td>
              <td className="px-3 py-2 text-right font-semibold" style={{ color: "#00d18c" }}>
                {fmtSigned(r.sector_rel20_pct)}%
              </td>
              <td className="px-3 py-2 text-xs">
                {r.news_articles != null && r.news_articles > 0 ? (
                  <span className="flex items-center gap-1.5">
                    <span title="News tone (GDELT)" style={{ color: toneColor(r.news_tone), fontWeight: 700 }}>
                      {r.news_tone != null ? fmtSigned(r.news_tone) : "•"}
                    </span>
                    <span style={{ color: "var(--text-muted, #64748b)" }}>· {r.news_articles} art</span>
                  </span>
                ) : (
                  <span style={{ color: "var(--text-muted, #64748b)" }}>—</span>
                )}
              </td>
              <td className="px-3 py-2 text-right font-bold" style={{ color: "#22d3ee" }}>{fmt(r.quality_score, 1)}</td>
              <td className="px-3 py-2"><TierBadge tier={r.tier} /></td>
              <td className="px-3 py-2 text-right">{fmt(r.close)}</td>
              <td className="px-3 py-2 text-right" style={{ color: (r.mom_long_pct ?? 0) >= 0 ? "#00d18c" : "#ff4d4d" }}>
                {fmtSigned(r.mom_long_pct)}%
              </td>
              <td className="px-3 py-2 text-right">{fmt(r.from_high_pct, 1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LockedTeaser({ result }: { result: ScreenerResult }) {
  const tiers = result.tiers ?? { quality: 0, momentum: 0, speculative: 0 };
  return (
    <div className="rounded-xl border p-8 text-center"
      style={{ borderColor: "rgba(0,212,255,0.15)", background: "rgba(15,23,42,0.5)" }}>
      <div className="w-12 h-12 mx-auto rounded-full flex items-center justify-center mb-3"
        style={{ background: "rgba(0,212,255,0.1)" }}>
        <Lock size={20} style={{ color: "#22d3ee" }} />
      </div>
      <h3 className="text-lg font-bold" style={{ color: "var(--text-primary, #e2e8f0)" }}>
        {result.hits} stocks match this screen
      </h3>
      <div className="flex items-center justify-center gap-2 mt-3 mb-1 flex-wrap">
        <span className="px-3 py-1 rounded-full text-xs font-semibold"
          style={{ background: TIER_STYLE.quality.bg, color: TIER_STYLE.quality.fg }}>
          {tiers.quality} Quality
        </span>
        <span className="px-3 py-1 rounded-full text-xs font-semibold"
          style={{ background: TIER_STYLE.momentum.bg, color: TIER_STYLE.momentum.fg }}>
          {tiers.momentum} Momentum
        </span>
        <span className="px-3 py-1 rounded-full text-xs font-semibold"
          style={{ background: TIER_STYLE.speculative.bg, color: TIER_STYLE.speculative.fg }}>
          {tiers.speculative} Speculative
        </span>
      </div>

      <div className="max-w-sm mx-auto mt-5 mb-6 space-y-2">
        {(result.sample_locked ?? []).slice(0, 4).map((s, i) => (
          <div key={i} className="flex items-center justify-between px-4 py-2 rounded-lg"
            style={{ background: "rgba(15,23,42,0.8)" }}>
            <span className="font-semibold blur-sm select-none" style={{ color: "var(--text-primary, #e2e8f0)" }}>
              ●●●●●●
            </span>
            <span className="flex items-center gap-2">
              <TierBadge tier={s.tier} />
              <span className="font-bold" style={{ color: "#22d3ee" }}>{fmt(s.quality_score, 1)}</span>
            </span>
          </div>
        ))}
      </div>

      <Link href="/login"
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold"
        style={{ background: "linear-gradient(135deg,#22d3ee,#0ea5e9)", color: "#04111f" }}>
        <LogIn size={16} /> Log in to unlock
      </Link>
      {result.as_of && (
        <p className="mt-3 text-xs" style={{ color: "var(--text-muted, #94a3b8)" }}>
          Confirmed at close · {result.as_of}
        </p>
      )}
    </div>
  );
}
