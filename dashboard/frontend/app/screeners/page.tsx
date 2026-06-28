"use client";
/**
 * /screeners — Technical Screeners (scanner suite).
 *
 * Pure read of pre-computed Redis snapshots (served by /api/screeners). The page
 * never triggers a scan, so it stays instant under any load. Logged-in users see
 * full ranked rows; anonymous users see a locked teaser (counts + tiers) that
 * prompts them to log in.
 */
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Radar, Lock, Download, RefreshCw, TrendingUp, AlertTriangle, LogIn } from "lucide-react";
import { api, ScreenerCatalogItem, ScreenerResult, ScreenerRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const SCANNER_NAME = "supertrend_flip";
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

function fmtVol(v: number): string {
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
}

function TierBadge({ tier }: { tier: string }) {
  const s = TIER_STYLE[tier] || TIER_STYLE.speculative;
  return (
    <span className="px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ background: s.bg, color: s.fg }}>{s.label}</span>
  );
}

function toCsv(rows: ScreenerRow[]): string {
  const head = ["Symbol", "Score", "Tier", "Flip", "Close", "Stop", "EMA10", "From52wHigh%", "RiskToStop%", "1mMom%", "AvgVol"];
  const lines = rows.map((r) => [
    r.symbol, r.quality_score, r.tier, r.flip, r.close, r.stop, r.ema10,
    r.from_high_pct, r.risk_to_stop_pct, r.mom_long_pct, r.avg_vol,
  ].join(","));
  return [head.join(","), ...lines].join("\n");
}

export default function ScreenersPage() {
  const { user, token } = useAuth();
  const entitled = !!user;   // any logged-in user gets full results

  const [tf, setTf] = useState("1W");
  const [catalog, setCatalog] = useState<ScreenerCatalogItem[]>([]);
  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState<string>("all");

  useEffect(() => {
    api.screenersCatalog().then((c) => setCatalog(c.scanners)).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.screener(SCANNER_NAME, tf, token);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [tf, token]);

  useEffect(() => { load(); }, [load]);

  const rows = result?.rows ?? [];
  const visibleRows = tierFilter === "all" ? rows : rows.filter((r) => r.tier === tierFilter);

  const exportCsv = () => {
    if (!rows.length) return;
    const blob = new Blob([toCsv(rows)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${SCANNER_NAME}_${tf}_${result?.as_of ?? "latest"}.csv`;
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
          <p className="text-xs" style={{ color: "var(--text-muted, #94a3b8)" }}>
            Supertrend(10,3) red→green flip, above 10 EMA — ranked by confluence quality
          </p>
        </div>
      </div>

      {/* Timeframe tabs + actions */}
      <div className="flex items-center justify-between flex-wrap gap-3 mt-4 mb-3">
        <div className="flex gap-1 p-1 rounded-lg" style={{ background: "rgba(15,23,42,0.6)" }}>
          {TIMEFRAMES.map((t) => {
            const cat = catalog.find((c) => c.name === SCANNER_NAME && c.timeframe === t.tf);
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

      {/* Tier filter (entitled with rows) */}
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

      {/* Locked teaser */}
      {result && result.locked && !result.pending && (
        <LockedTeaser result={result} />
      )}

      {/* Full table */}
      {result && !result.locked && !result.pending && (
        rows.length === 0 ? (
          <div className="py-16 text-center text-sm" style={{ color: "var(--text-muted, #94a3b8)" }}>
            No fresh flips in the liquid universe right now. Check the other timeframe.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border"
            style={{ borderColor: "var(--border, rgba(0,212,255,0.08))" }}>
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
                {visibleRows.map((r, i) => (
                  <tr key={r.symbol} className="border-t"
                    style={{ borderColor: "rgba(255,255,255,0.04)" }}>
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
        )
      )}

      {/* Disclaimer */}
      <p className="mt-4 text-xs" style={{ color: "var(--text-muted, #64748b)" }}>
        <TrendingUp size={11} className="inline mr-1" />
        Signals are a screen, not advice. A Supertrend flip is a probability edge — size for the stop and do your own analysis.
      </p>
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

      {/* Blurred sample rows */}
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
