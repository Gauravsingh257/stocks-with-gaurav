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
import { api, type DecisionPortfoliosPayload, type WatchlistIntelItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { subscribeWatchlistOsHint } from "@/lib/watchlistOsBridge";

export default function WatchlistPage() {
  const { user, token } = useAuth();
  const [intel, setIntel] = useState<WatchlistIntelItem[]>([]);
  const [savedSymbols, setSavedSymbols] = useState<{ symbol: string; added_at?: string }[]>([]);
  const [feed, setFeed] = useState<{ headline?: string; symbol?: string; ts?: string }[]>([]);
  const [retention, setRetention] = useState<Record<string, string[]> | null>(null);
  const [marketAlign, setMarketAlign] = useState<Record<string, unknown> | null>(null);
  const [decisionPortfolios, setDecisionPortfolios] = useState<DecisionPortfoliosPayload | null>(null);
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
      try {
        await api.intelEvent(token, { event: "watchlist_open" });
      } catch {
        /* non-fatal */
      }
      setIntel(data.items || []);
      setSavedSymbols(wl.items || []);
      setFeed(data.feed || []);
      setRetention(data.retention || null);
      setMarketAlign(data.market_alignment || null);
      setDecisionPortfolios(data.decision_portfolios ?? null);
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

  useEffect(() => {
    const uid = user?.id;
    if (uid == null) return;
    let deb: ReturnType<typeof setTimeout> | undefined;
    const unsub = subscribeWatchlistOsHint(({ userId }) => {
      if (userId !== uid) return;
      if (deb) clearTimeout(deb);
      deb = setTimeout(() => {
        loadOperating();
      }, 450);
    });
    return () => {
      unsub();
      if (deb) clearTimeout(deb);
    };
  }, [user?.id, loadOperating]);

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
  const weakeningBand = intel.filter((x) => x.intelligence?.setup_deteriorating);
  const nearEntryBand = intel.filter((x) => x.setup_status === "NEAR_ENTRY" || x.setup_status === "READY");
  const topReadiness = [...intel].sort((a, b) => (b.readiness_pct ?? 0) - (a.readiness_pct ?? 0)).slice(0, 8);
  const bestRrBand = [...intel]
    .filter((x) => (x.risk?.rr ?? 0) > 0)
    .sort((a, b) => (Number(b.risk?.rr) || 0) - (Number(a.risk?.rr) || 0))
    .slice(0, 8);
  const improvingBand = [...intel]
    .filter((x) => (x.readiness_delta_pct ?? 0) >= 1)
    .sort((a, b) => (b.readiness_delta_pct ?? 0) - (a.readiness_delta_pct ?? 0))
    .slice(0, 8);
  const momentumBand = intel.filter((x) => {
    const m = x.intelligence?.pillar_scores?.momentum;
    return (m ?? 0) >= 72 && (x.readiness_delta_pct ?? 0) > 0;
  });

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
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10, fontSize: "0.78rem" }}>
            <div><Zap size={12} style={{ display: "inline", color: "var(--warning)" }} /> Closest to trigger: {retention.closest_to_trigger?.join(", ") || "—"}</div>
            <div>Strongest scores: {retention.strongest_scores?.join(", ") || "—"}</div>
            <div>Best R:R: {retention.best_rr?.join(", ") || "—"}</div>
            <div style={{ color: "var(--success)" }}>Improving readiness: {retention.improving_readiness?.join(", ") || "—"}</div>
            <div style={{ color: "var(--accent)" }}>Momentum expansion: {retention.momentum_expansion?.join(", ") || "—"}</div>
            <div style={{ color: "var(--danger)" }}>Weakening: {retention.weakening?.join(", ") || "—"}</div>
            <div style={{ color: "var(--danger)" }}>Invalidated: {retention.invalidated?.join(", ") || "—"}</div>
            <div style={{ color: "var(--success)" }}>Active (engine): {retention.active?.join(", ") || "—"}</div>
          </div>
        </div>
      )}

      {decisionPortfolios && (
        <div className="glass" style={{ padding: 14, border: "1px solid rgba(0,212,255,0.12)" }}>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 10 }}>
            VIRTUAL DESKS (ranked by allocation score — not executed allocation)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10, fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            <div><span style={{ color: "var(--text-dim)" }}>Intraday momentum</span><br /><strong style={{ color: "var(--text-primary)" }}>{(decisionPortfolios.intraday_momentum ?? []).slice(0, 12).join(", ") || "—"}</strong></div>
            <div><span style={{ color: "var(--text-dim)" }}>MTF swing</span><br /><strong style={{ color: "var(--text-primary)" }}>{(decisionPortfolios.mtf_swing ?? []).slice(0, 12).join(", ") || "—"}</strong></div>
            <div><span style={{ color: "var(--text-dim)" }}>Short-term growth</span><br /><strong style={{ color: "var(--text-primary)" }}>{(decisionPortfolios.short_term_growth ?? []).slice(0, 12).join(", ") || "—"}</strong></div>
            <div><span style={{ color: "var(--text-dim)" }}>Long-term compounders</span><br /><strong style={{ color: "var(--text-primary)" }}>{(decisionPortfolios.long_term_compounders ?? []).slice(0, 12).join(", ") || "—"}</strong></div>
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

          {(nearEntryBand.length > 0 ||
            topReadiness.length > 0 ||
            improvingBand.length > 0 ||
            momentumBand.length > 0 ||
            weakeningBand.length > 0 ||
            bad.length > 0 ||
            ready.length > 0 ||
            bestRrBand.length > 0) && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 12 }}>TERMINAL LENSES</div>
              {nearEntryBand.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--warning)", marginBottom: 6 }}>Near entry / executable window</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {nearEntryBand.map((x) => (
                      <span
                        key={`ne-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(245,158,11,0.08)",
                          border: "1px solid rgba(245,158,11,0.25)",
                        }}
                      >
                        {x.symbol}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {improvingBand.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--success)", marginBottom: 6 }}>Improving setups (Δ readiness)</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {improvingBand.map((x) => (
                      <span
                        key={`im-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(0,224,150,0.06)",
                          border: "1px solid rgba(0,224,150,0.22)",
                        }}
                      >
                        {x.symbol} Δ{x.readiness_delta_pct != null ? `+${x.readiness_delta_pct}` : "—"}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {momentumBand.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--accent)", marginBottom: 6 }}>Momentum expansion</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {momentumBand.map((x) => (
                      <span
                        key={`mo-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(0,212,255,0.06)",
                          border: "1px solid rgba(0,212,255,0.18)",
                        }}
                      >
                        {x.symbol} · Mom {Math.round(x.intelligence?.pillar_scores?.momentum ?? 0)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {topReadiness.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--accent)", marginBottom: 6 }}>Highest readiness</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {topReadiness.map((x) => (
                      <span
                        key={`hr-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(0,212,255,0.08)",
                          border: "1px solid rgba(0,212,255,0.22)",
                        }}
                      >
                        {x.symbol} · {x.readiness_pct ?? "—"}%
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {weakeningBand.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--danger)", marginBottom: 6 }}>Weakening setups</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {weakeningBand.map((x) => (
                      <span
                        key={`wk-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(239,68,68,0.06)",
                          border: "1px solid rgba(239,68,68,0.22)",
                        }}
                      >
                        {x.symbol}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {bad.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--danger)", marginBottom: 6 }}>Invalidated</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {bad.map((x) => (
                      <span
                        key={`inv-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(239,68,68,0.04)",
                          border: "1px solid rgba(239,68,68,0.35)",
                        }}
                      >
                        {x.symbol}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {ready.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: "0.62rem", color: "var(--success)", marginBottom: 6 }}>Active / ready (desk)</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {ready.map((x) => (
                      <span
                        key={`rd-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(0,224,150,0.08)",
                          border: "1px solid rgba(0,224,150,0.25)",
                        }}
                      >
                        {x.symbol}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {bestRrBand.length > 0 && (
                <div>
                  <div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 6 }}>Best R:R (research)</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {bestRrBand.map((x) => (
                      <span
                        key={`rr-${x.symbol}`}
                        style={{
                          fontSize: "0.72rem",
                          padding: "4px 10px",
                          borderRadius: 999,
                          background: "rgba(255,255,255,0.04)",
                          border: "1px solid rgba(255,255,255,0.08)",
                        }}
                      >
                        {x.symbol} · {x.risk?.rr != null ? Number(x.risk.rr).toFixed(2) : "—"}R
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>SETUP GRID (ranked by AI score)</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {intel.map((row) => {
              const rec = row.recommendation;
              const showActionableLevels = Boolean(row.entry_ready && rec?.entry_ready);
              const ps = row.intelligence?.pillar_scores;
              const dec = row.decision;
              const showDecision = Boolean(dec && dec.note !== "decision_intel_unavailable");
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
                    {row.desk_os && (
                      <div
                        style={{
                          marginTop: 8,
                          display: "flex",
                          flexWrap: "wrap",
                          gap: 6,
                          alignItems: "center",
                          fontSize: "0.68rem",
                          color: "var(--text-secondary)",
                        }}
                      >
                        <span
                          style={{
                            padding: "3px 10px",
                            borderRadius: 999,
                            border: "1px solid rgba(0,212,255,0.22)",
                            background: "rgba(0,212,255,0.06)",
                            fontWeight: 700,
                          }}
                        >
                          #{row.desk_os.evolving_rank}{" "}
                          {String(row.desk_os.urgency_state ?? "monitor").replace(/_/g, " ")}
                        </span>
                        {row.desk_os.capital_priority_score != null && (
                          <span style={{ color: "var(--text-dim)" }}>Priority {Math.round(row.desk_os.capital_priority_score)}</span>
                        )}
                        {row.desk_os.deterioration_risk_trend && (
                          <span style={{ color: "var(--text-dim)" }}>Risk trend {row.desk_os.deterioration_risk_trend}</span>
                        )}
                        {row.desk_os.execution_timing_quality && (
                          <span style={{ color: "var(--text-dim)" }}>Timing {row.desk_os.execution_timing_quality}</span>
                        )}
                        {row.desk_os.expected_opportunity_window && (
                          <span style={{ color: "var(--text-dim)" }}>Window {row.desk_os.expected_opportunity_window}</span>
                        )}
                      </div>
                    )}
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
                  <div>
                    LTP:{" "}
                    <strong>{row.quote_ltp != null ? `₹${Number(row.quote_ltp).toFixed(2)}` : "—"}</strong>
                  </div>
                  <div>
                    Last engine tick:{" "}
                    <span style={{ color: "var(--text-dim)" }}>
                      {row.meta?.engine_tick_ts ? String(row.meta.engine_tick_ts).slice(11, 19) : "—"}
                    </span>
                  </div>
                  <div>
                    Readiness: <strong>{row.readiness_pct ?? "—"}%</strong>
                    {row.readiness_delta_pct != null && row.readiness_delta_pct !== 0 ? (
                      <span style={{ color: row.readiness_delta_pct > 0 ? "var(--success)" : "var(--danger)", marginLeft: 6 }}>
                        ({row.readiness_delta_pct > 0 ? "+" : ""}
                        {row.readiness_delta_pct})
                      </span>
                    ) : null}
                  </div>
                  <div>
                    Confidence: <strong>{row.conviction_pct ?? "—"}%</strong>
                  </div>
                </div>

                {row.intelligence?.stage_reason && (
                  <div style={{ marginTop: 10, fontSize: "0.76rem", color: "var(--text-secondary)", lineHeight: 1.45 }}>
                    <strong style={{ color: "var(--text-dim)" }}>Desk</strong> — {row.intelligence.stage_reason}
                  </div>
                )}
                {ps && (
                  <div style={{ marginTop: 8, fontSize: "0.68rem", color: "var(--text-dim)", lineHeight: 1.5 }}>
                    Pillars · S {Math.round(ps.structure ?? 0)} · Mom {Math.round(ps.momentum ?? 0)} · Liq {Math.round(ps.liquidity ?? 0)} · Vol{" "}
                    {Math.round(ps.volume ?? 0)} · RR {Math.round(ps.rr ?? 0)} · HTF {Math.round(ps.htf ?? 0)} · Vx {Math.round(ps.volatility_context ?? 0)}
                  </div>
                )}
                {(row.intelligence?.risk_grade || row.intelligence?.setup_quality) && (
                  <div style={{ marginTop: 6, fontSize: "0.72rem", color: "var(--text-secondary)" }}>
                    Risk grade <strong>{row.intelligence?.risk_grade ?? "—"}</strong> · Setup quality{" "}
                    <strong>{row.intelligence?.setup_quality ?? "—"}</strong>
                    {row.intelligence?.next_trigger ? (
                      <span style={{ display: "block", marginTop: 4, color: "var(--text-dim)" }}>Next trigger: {row.intelligence.next_trigger}</span>
                    ) : null}
                  </div>
                )}
                {row.intelligence?.setup_deteriorating && (
                  <div style={{ marginTop: 8, fontSize: "0.74rem", color: "var(--warning)" }}>
                    Setup weakening — structure vs plan needs attention.
                  </div>
                )}

                {showDecision && dec && (
                  <div
                    style={{
                      marginTop: 10,
                      padding: 10,
                      borderRadius: 8,
                      background: "rgba(0,212,255,0.04)",
                      border: "1px solid rgba(0,212,255,0.14)",
                      fontSize: "0.72rem",
                      lineHeight: 1.55,
                      color: "var(--text-secondary)",
                    }}
                  >
                    <div style={{ fontWeight: 800, color: "var(--accent)", marginBottom: 6 }}>Decision intelligence</div>
                    <div>
                      <strong style={{ color: "var(--text-primary)" }}>{dec.setup_maturity_stage ?? "—"}</strong>
                      {dec.setup_maturity_score != null ? ` · maturity ${dec.setup_maturity_score}` : ""}
                      {dec.signal_tier ? ` · tier ${dec.signal_tier}` : ""}
                    </div>
                    {dec.readiness_evolution?.evolution_regime ? (
                      <div style={{ marginTop: 4 }}>
                        Readiness evolution: <strong>{dec.readiness_evolution.evolution_regime}</strong>
                        {dec.readiness_evolution.readiness_acceleration != null
                          ? ` (Δ vs window ${dec.readiness_evolution.readiness_acceleration >= 0 ? "+" : ""}${dec.readiness_evolution.readiness_acceleration})`
                          : ""}
                      </div>
                    ) : null}
                    <div style={{ marginTop: 4 }}>
                      Modelled lifecycle continuation ~{dec.lifecycle_probability_pct ?? "—"}% · stress ~{dec.deterioration_probability_pct ?? "—"}%
                    </div>
                    <div style={{ marginTop: 4 }}>
                      Execution: {dec.execution_quality?.band ?? "—"} · Capital priority: {dec.allocation?.label ?? "—"} · Horizon hint:{" "}
                      {dec.holding_period_hint ?? "—"}
                    </div>
                    {dec.user_action?.primary_action ? (
                      <div style={{ marginTop: 6, fontWeight: 700, color: "var(--text-primary)" }}>
                        Now: {dec.user_action.primary_action}
                      </div>
                    ) : null}
                    {dec.execution_realism?.execution_posture ? (
                      <div style={{ marginTop: 4, color: "var(--text-dim)" }}>
                        Execution posture: <strong style={{ color: "var(--text-secondary)" }}>{dec.execution_realism.execution_posture}</strong>
                        {dec.execution_realism.distance_from_entry_pct != null
                          ? ` · ~${dec.execution_realism.distance_from_entry_pct}% from plan entry`
                          : ""}
                        {dec.execution_realism.rr_degradation != null && dec.execution_realism.rr_degradation > 0
                          ? ` · RR erosion ~${dec.execution_realism.rr_degradation}`
                          : ""}
                      </div>
                    ) : null}
                    {dec.confidence_calibration ? (
                      <div style={{ marginTop: 4, fontSize: "0.68rem", color: "var(--text-dim)" }}>
                        Calibrated tier lens — INST ~{dec.confidence_calibration.inst_probability_pct ?? "—"}% · HIGH ~
                        {dec.confidence_calibration.high_probability_pct ?? "—"}% · stress/fail ~{dec.confidence_calibration.failure_probability_pct ?? "—"}%
                        {dec.confidence_calibration.note ? ` — ${dec.confidence_calibration.note}` : ""}
                      </div>
                    ) : null}
                    {dec.deterioration_validation?.validated_overall ? (
                      <div style={{ marginTop: 4, color: "var(--warning)" }}>
                        Validation: {dec.deterioration_validation.validated_overall}
                        {dec.deterioration_validation.failed_continuation ? " · failed-continuation context" : ""}
                      </div>
                    ) : null}
                    {dec.trade_levels && dec.trade_levels.show_actionable_levels === false ? (
                      <div style={{ marginTop: 6, color: "var(--warning)", fontSize: "0.7rem" }}>
                        Levels withheld ({dec.trade_levels.zone_reason ?? "gates"}) — {dec.trade_levels.monitoring_copy ?? "monitor structure vs plan."}
                      </div>
                    ) : null}
                    {dec.time_decay?.setup_expired_hint ? (
                      <div style={{ marginTop: 4, color: "var(--warning)", fontSize: "0.68rem" }}>
                        Time decay: stagnation suggests reassessing urgency — not an auto-expiry.
                      </div>
                    ) : null}
                    {dec.deterioration?.overall_risk_tone ? (
                      <div style={{ marginTop: 4, color: dec.deterioration.capital_protection_warning ? "var(--warning)" : "var(--text-dim)" }}>
                        Risk tone: {dec.deterioration.overall_risk_tone}
                        {dec.deterioration.false_breakout_probability_pct != null
                          ? ` · false-break ~${dec.deterioration.false_breakout_probability_pct}%`
                          : ""}
                      </div>
                    ) : null}
                  </div>
                )}

                {row.setup_status === "INVALIDATED" && row.recommendation?.invalidation_reason ? (
                  <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.35)", fontSize: "0.8rem", color: "var(--danger)" }}>
                    <strong>Setup invalidated</strong>
                    <p style={{ margin: "6px 0 0", lineHeight: 1.45 }}>{row.recommendation.invalidation_reason}</p>
                  </div>
                ) : showActionableLevels && rec ? (
                  <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(0,224,150,0.06)", border: "1px solid rgba(0,224,150,0.2)", fontSize: "0.78rem" }}>
                    <div style={{ fontWeight: 800, marginBottom: 6, color: "var(--success)" }}>Actionable levels (research + engine)</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
                      <span>Entry ₹{rec.entry != null ? Number(rec.entry).toFixed(2) : "—"}</span>
                      <span>SL ₹{rec.stop_loss != null ? Number(rec.stop_loss).toFixed(2) : "—"}</span>
                      <span>Tgt ₹{rec.target != null ? Number(rec.target).toFixed(2) : "—"}</span>
                    </div>
                    {rec.rationale ? <p style={{ margin: "8px 0 0", color: "var(--text-secondary)", lineHeight: 1.45 }}>{rec.rationale}</p> : null}
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
