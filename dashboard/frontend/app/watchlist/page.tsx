"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  Bookmark,
  Trash2,
  ExternalLink,
  Activity,
  Radar,
  Zap,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { api, type WatchlistIntelItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function WatchlistPage() {
  const { user, token } = useAuth();
  const [intel, setIntel] = useState<WatchlistIntelItem[]>([]);
  const [savedSymbols, setSavedSymbols] = useState<{ symbol: string; added_at?: string }[]>([]);
  const [feed, setFeed] = useState<{ headline?: string; symbol?: string; ts?: string }[]>([]);
  const [retention, setRetention] = useState<Record<string, string[]> | null>(null);
  const [marketAlign, setMarketAlign] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadOperating = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [data, wl] = await Promise.all([
        api.getWatchlistOperating(token),
        api.getWatchlist(token),
      ]);
      setIntel(data.items || []);
      setSavedSymbols(wl.items || []);
      setFeed(data.feed || []);
      setRetention(data.retention || null);
      setMarketAlign(data.market_alignment || null);
    } catch {
      setError("Could not load watchlist intelligence.");
      setIntel([]);
      setSavedSymbols([]);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadOperating();
  }, [loadOperating]);

  const handleRemove = async (symbol: string) => {
    if (!token) return;
    await api.removeFromWatchlist(token, symbol);
    setIntel((prev) => prev.filter((x) => x.symbol !== symbol));
  };

  if (!user) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <Bookmark size={40} color="var(--text-dim)" style={{ margin: "0 auto 16px" }} />
        <h2 style={{ fontSize: "1.2rem", marginBottom: 8 }}>Sign in to use Watchlist</h2>
        <p style={{ color: "var(--text-secondary)", marginBottom: 20 }}>Save stocks and get AI-style setup tracking from live research + engine data.</p>
        <Link href="/login" className="btn-accent" style={{ textDecoration: "none", padding: "10px 24px" }}>Sign In</Link>
      </div>
    );
  }

  const ready = intel.filter((x) => x.setup_status === "READY" || x.setup_status === "ACTIVE");
  const near = intel.filter((x) => x.setup_status === "NEAR_ENTRY" || x.setup_status === "FORMING");
  const bad = intel.filter((x) => x.setup_status === "INVALIDATED");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 1200, margin: "0 auto" }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 40, height: 40, borderRadius: 10, background: "rgba(0,212,255,0.1)", border: "1px solid rgba(0,212,255,0.2)", display: "grid", placeItems: "center" }}>
            <Radar size={20} color="var(--accent)" />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: "1.35rem", fontWeight: 800 }}>AI Watchlist Terminal</h1>
            <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: "0.8rem" }}>
              {Math.max(intel.length, savedSymbols.length)} saved · {intel.length} with live prep intelligence
            </p>
          </div>
        </div>
        <button type="button" onClick={loadOperating} className="btn-accent" style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 14px", fontSize: "0.8rem" }}>
          <RefreshCw size={14} /> Sync
        </button>
      </div>

      {marketAlign && (
        <div className="glass" style={{ padding: 12, display: "flex", flexWrap: "wrap", gap: 12, fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          <span><Activity size={12} style={{ display: "inline", verticalAlign: "middle" }} /> Regime: <strong style={{ color: "var(--text-primary)" }}>{String(marketAlign.market_regime ?? "—")}</strong></span>
          <span>Engine: <strong>{marketAlign.engine_live ? "live" : "idle"}</strong></span>
          <span>Signals today: <strong>{String(marketAlign.signals_today ?? "—")}</strong></span>
        </div>
      )}

      {retention && (
        <div className="glass" style={{ padding: 14, border: "1px solid rgba(0,224,150,0.15)" }}>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1, marginBottom: 8 }}>TODAY&apos;S FOCUS</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10, fontSize: "0.78rem" }}>
            <div><Zap size={12} style={{ display: "inline", color: "var(--warning)" }} /> Closest to trigger: {retention.closest_to_trigger?.join(", ") || "—"}</div>
            <div>Strongest scores: {retention.strongest_scores?.join(", ") || "—"}</div>
            <div>Best R:R: {retention.best_rr?.join(", ") || "—"}</div>
            <div style={{ color: "var(--danger)" }}>Invalidated: {retention.invalidated?.join(", ") || "—"}</div>
          </div>
        </div>
      )}

      {error && <div className="glass" style={{ padding: 12, color: "var(--danger)", fontSize: "0.85rem" }}>{error}</div>}

      {loading ? (
        <div className="glass" style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>Loading watchlist operating system…</div>
      ) : intel.length === 0 && savedSymbols.length === 0 ? (
        <div className="glass" style={{ padding: "32px 24px", textAlign: "center" }}>
          <Bookmark size={32} color="var(--text-dim)" style={{ margin: "0 auto 12px" }} />
          <p style={{ color: "var(--text-secondary)", marginBottom: 8 }}>Your watchlist is empty.</p>
          <p style={{ color: "var(--text-dim)", fontSize: "0.82rem" }}>
            Add from <Link href="/research" style={{ color: "var(--accent)" }}>Research</Link>, stock pages, or the global search card.
          </p>
        </div>
      ) : intel.length === 0 && savedSymbols.length > 0 ? (
        <div className="glass" style={{ padding: "24px", textAlign: "center", color: "var(--text-secondary)" }}>
          <p style={{ marginBottom: 12 }}>Saved symbols: {savedSymbols.map((s) => s.symbol).join(", ")}</p>
          <p style={{ fontSize: "0.82rem", color: "var(--text-dim)" }}>Preparing intelligence… tap Sync or wait a moment.</p>
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
            <div className="glass" style={{ padding: 12, textAlign: "center" }}>
              <div style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>Executable window</div>
              <div style={{ fontSize: "1.25rem", fontWeight: 900, color: "var(--success)" }}>{ready.length}</div>
            </div>
            <div className="glass" style={{ padding: 12, textAlign: "center" }}>
              <div style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>Building</div>
              <div style={{ fontSize: "1.25rem", fontWeight: 900, color: "var(--warning)" }}>{near.length}</div>
            </div>
            <div className="glass" style={{ padding: 12, textAlign: "center" }}>
              <div style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>Stale / invalid</div>
              <div style={{ fontSize: "1.25rem", fontWeight: 900, color: "var(--danger)" }}>{bad.length}</div>
            </div>
          </div>

          {feed.length > 0 && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(0,212,255,0.14)" }}>
              <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 10 }}>LIVE INTELLIGENCE FEED</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 220, overflowY: "auto" }}>
                {feed.map((f, i) => (
                  <div key={`${f.ts}-${i}`} style={{ fontSize: "0.78rem", borderBottom: "1px solid rgba(255,255,255,0.06)", paddingBottom: 6 }}>
                    <span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>{f.ts?.slice(11, 19) || ""}</span>{" "}
                    <span style={{ color: "var(--accent)", fontWeight: 700 }}>{f.symbol}</span> — {f.headline}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>SETUP GRID (ranked by AI score)</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {intel.map((row) => {
              const tradeLevelsOk = Boolean(row.recommendation?.entry_ready ?? row.recommendation?.show_trade_levels);
              return (
              <div key={row.symbol} className="glass" style={{ padding: 14, border: "1px solid rgba(255,255,255,0.06)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
                  <div>
                    <a
                      href={`https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(row.symbol)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "var(--accent)", fontWeight: 800, fontSize: "1.05rem", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}
                    >
                      {row.symbol} <ExternalLink size={12} />
                    </a>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginTop: 4 }}>
                      {row.lifecycle_stage || row.setup_status || "—"} · {row.horizon || "—"} ·{" "}
                      <span style={{ color: "var(--text-secondary)" }}>{row.trend_state}</span> ·{" "}
                      <strong style={{ color: row.setup_status === "READY" ? "var(--success)" : "var(--warning)" }}>{row.setup_status}</strong>
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>AI Setup Score</div>
                    <div style={{ fontSize: "1.4rem", fontWeight: 900, color: "var(--accent)" }}>{row.ai_setup_score ?? "—"}</div>
                    <button type="button" onClick={() => handleRemove(row.symbol)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)", marginTop: 4 }} title="Remove">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(row.progression || []).map((p) => (
                    <span
                      key={p.id}
                      style={{
                        fontSize: "0.65rem",
                        padding: "3px 8px",
                        borderRadius: 6,
                        border: "1px solid rgba(255,255,255,0.08)",
                        background: p.status === "complete" ? "rgba(0,224,150,0.1)" : "rgba(255,255,255,0.03)",
                        color: p.status === "complete" ? "var(--success)" : "var(--text-dim)",
                      }}
                    >
                      {p.status === "complete" ? "✓ " : p.status === "waiting" ? "⏳ " : "◌ "}
                      {p.label}
                    </span>
                  ))}
                </div>

                <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8, fontSize: "0.78rem" }}>
                  <div>Readiness: <strong>{row.readiness_pct ?? "—"}%</strong></div>
                  <div>Conviction: <strong>{row.conviction_pct ?? "—"}%</strong></div>
                </div>

                {row.setup_status === "INVALIDATED" && row.recommendation?.invalidation_reason ? (
                  <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.35)", fontSize: "0.8rem", color: "var(--danger)" }}>
                    <strong>Setup invalidated</strong>
                    <p style={{ margin: "6px 0 0", lineHeight: 1.45 }}>{row.recommendation.invalidation_reason}</p>
                  </div>
                ) : tradeLevelsOk ? (
                  <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(0,224,150,0.06)", border: "1px solid rgba(0,224,150,0.2)", fontSize: "0.78rem" }}>
                    <div style={{ fontWeight: 800, marginBottom: 6, color: "var(--success)" }}>Actionable levels (research + engine)</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
                      <span>Entry ₹{row.recommendation.entry != null ? Number(row.recommendation.entry).toFixed(2) : "—"}</span>
                      <span>SL ₹{row.recommendation.stop_loss != null ? Number(row.recommendation.stop_loss).toFixed(2) : "—"}</span>
                      <span>Tgt ₹{row.recommendation.target != null ? Number(row.recommendation.target).toFixed(2) : "—"}</span>
                    </div>
                    {row.recommendation.rationale && <p style={{ margin: "8px 0 0", color: "var(--text-secondary)", lineHeight: 1.45 }}>{row.recommendation.rationale}</p>}
                  </div>
                ) : row.setup_status === "INVALIDATED" ? (
                  <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(239,68,68,0.06)", border: "1px dashed rgba(239,68,68,0.35)", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    <strong style={{ color: "var(--danger)" }}>Invalidated</strong> — structure no longer qualifies for an actionable setup this cycle.
                  </div>
                ) : (
                  <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(245,158,11,0.06)", border: "1px dashed rgba(245,158,11,0.3)", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    <strong style={{ color: "var(--warning)" }}>Monitoring</strong> — {row.recommendation?.monitoring_message || "Structure under observation."}
                    {row.recommendation?.nearest_trigger && (
                      <div style={{ marginTop: 6, fontSize: "0.74rem" }}>Nearest trigger: {row.recommendation.nearest_trigger}</div>
                    )}
                  </div>
                )}

                <Link href={`/research`} style={{ marginTop: 10, display: "inline-flex", alignItems: "center", gap: 4, fontSize: "0.72rem", color: "var(--accent)" }}>
                  Research context <ChevronRight size={12} />
                </Link>
              </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
