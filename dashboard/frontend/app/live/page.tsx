"use client";
/**
 * /live — Live Trading Page
 * Real-time engine snapshot via WebSocket + REST fallback.
 */
import { useEngineSocket } from "@/lib/useWebSocket";
import type { AdaptiveIntel, SetupDEntry } from "@/lib/api";
import { ShieldAlert, TrendingUp, TrendingDown, Clock, Target, Shield } from "lucide-react";
import { HeroBanner, TickerStrip } from "@/components/FuturisticElements";
import { DisplacementMonitor } from "./DisplacementMonitor";

function pnlColor(v: number) { return v >= 0 ? "var(--success)" : "var(--danger)"; }
function dirColor(d: string) { return d === "LONG" ? "var(--accent)" : "var(--warning)"; }

export default function LivePage() {
  const { snapshot, status } = useEngineSocket();

  if (!snapshot) {
    return (
      <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {/* Hero Banner always visible */}
        <HeroBanner />
        <TickerStrip />

        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "40vh", flexDirection: "column", gap: 12 }}>
          <div style={{ width: 40, height: 40, borderRadius: "50%", border: "3px solid var(--accent)", borderTopColor: "transparent", animation: "spin 0.8s linear infinite" }} />
          <span style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>
            {status === "polling" ? "Loading engine data..." : "Connecting to engine…"}
          </span>
          <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>
            {status === "disconnected" ? "WebSocket reconnecting — switching to REST polling soon" : 
             status === "polling" ? "Using REST fallback — data refreshes every 2s" : ""}
          </span>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      </div>
    );
  }

  const { active_trades, daily_pnl_r, consecutive_losses, signals_today,
          max_daily_signals, circuit_breaker_active, market_regime,
          engine_mode, active_strategies, paper_mode, index_only,
          zone_state, setup_d_state } = snapshot;

  const pnlStatus = daily_pnl_r <= -2.5 ? "CRITICAL" : daily_pnl_r <= -1.5 ? "WARNING" : "NORMAL";

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {/* Hero Banner */}
      <HeroBanner />

      {/* Ticker Strip */}
      <TickerStrip />

      {/* Page header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 className="text-xl md:text-2xl lg:text-3xl font-bold m-0" style={{ color: "var(--text-primary)" }}>Live Trading</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
            Real-time engine state · {new Date(snapshot.snapshot_time).toLocaleTimeString()}
          </p>
        </div>
        {circuit_breaker_active && (
          <div className="badge badge-halt" style={{ padding: "8px 16px", fontSize: "0.8rem" }}>
            <ShieldAlert size={14} /> CIRCUIT BREAKER ACTIVE
          </div>
        )}
      </div>

      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
        <StatCard label="Daily PnL" value={`${daily_pnl_r >= 0 ? "+" : ""}${daily_pnl_r.toFixed(2)}R`}
          valueColor={pnlColor(daily_pnl_r)}
          sub={pnlStatus !== "NORMAL" ? pnlStatus : "On track"}
          subColor={pnlStatus === "CRITICAL" ? "var(--danger)" : pnlStatus === "WARNING" ? "var(--warning)" : "var(--success)"}
        />
        <StatCard label="Active Trades" value={String(active_trades.length)} valueColor="var(--text-primary)" sub="positions open" />
        <StatCard label="Signals Today" value={`${signals_today}/${max_daily_signals}`} valueColor="var(--text-primary)"
          sub={signals_today >= max_daily_signals ? "CAP REACHED" : "remaining"} subColor={signals_today >= max_daily_signals ? "var(--danger)" : undefined}
        />
        <StatCard label="Consec Losses" value={String(consecutive_losses)} valueColor={consecutive_losses >= 3 ? "var(--danger)" : "var(--text-primary)"}
          sub={consecutive_losses >= 2 ? "Cooldown risk" : "Streak clean"} subColor={consecutive_losses >= 2 ? "var(--warning)" : "var(--success)"}
        />
        <StatCard label="Market Regime"
          value={market_regime}
          valueColor={market_regime === "BULLISH" ? "var(--success)" : market_regime === "BEARISH" ? "var(--danger)" : "var(--text-secondary)"}
          sub="1h + OI"
        />
        <StatCard label="Engine Mode" value={engine_mode} valueColor="var(--accent)" sub={paper_mode ? "Paper" : "Live"} />
      </div>

      {/* Tier 3: Adaptive Intelligence */}
      <AdaptiveIntelligencePanel adaptiveIntel={snapshot.adaptive_intel} />

      {/* Active strategies */}
      <div className="glass" style={{ padding: "12px 20px" }}>
        <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.08em", marginBottom: 8 }}>ACTIVE STRATEGIES</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(active_strategies).map(([k, v]) => (
            <span key={k} className={`badge ${v ? "badge-live" : "badge-neutral"}`}>{k}</span>
          ))}
          <span className={`badge ${index_only ? "badge-paper" : "badge-neutral"}`}>
            {index_only ? "INDEX ONLY" : "STOCKS ON"}
          </span>
        </div>
      </div>

      {/* Active trades */}
      <div className="glass" style={{ overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Active Trades</span>
          <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>{active_trades.length} open</span>
        </div>
        {active_trades.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
            No active positions
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Dir</th><th>Setup</th>
                  <th>Entry</th><th>SL</th><th>Target</th><th>RR</th>
                </tr>
              </thead>
              <tbody>
                {active_trades.map((t, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>{t.symbol.replace("NSE:", "")}</td>
                    <td>
                      <span className={`badge ${t.direction === "LONG" ? "badge-long" : "badge-short"}`}>
                        {t.direction === "LONG" ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                        {t.direction}
                      </span>
                    </td>
                    <td style={{ color: "var(--text-secondary)", fontSize: "0.78rem" }}>{t.setup}</td>
                    <td style={{ fontFamily: "monospace" }}>{t.entry}</td>
                    <td style={{ color: "var(--danger)", fontFamily: "monospace" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 4 }}><Shield size={11} />{t.sl}</span>
                    </td>
                    <td style={{ color: "var(--success)", fontFamily: "monospace" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 4 }}><Target size={11} />{t.target}</span>
                    </td>
                    <td style={{ color: t.rr >= 2 ? "var(--success)" : "var(--warning)" }}>{t.rr.toFixed(1)}R</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Setup-D Live Pipeline State (gap-day + BOS/WAIT/TAPPED) */}
      <SetupDStatePanel setupDState={setup_d_state ?? {}} />

      {/* Zone state */}
      <ZoneStatePanel zoneState={zone_state} />

      {/* Momentum Shift Monitor (Phase 2 — Early Displacement Detection) */}
      <DisplacementMonitor />
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({ label, value, valueColor, sub, subColor }: {
  label: string; value: string; valueColor?: string;
  sub?: string; subColor?: string;
}) {
  return (
    <div className="stat-card glow-card">
      <div style={{ fontSize: "0.68rem", color: "var(--text-secondary)", letterSpacing: "0.07em", marginBottom: 6 }}>{label.toUpperCase()}</div>
      <div style={{ fontSize: "1.4rem", fontWeight: 700, color: valueColor ?? "var(--text-primary)", lineHeight: 1 }}>{value}</div>
      {sub && (
        <div style={{ fontSize: "0.7rem", color: subColor ?? "var(--text-secondary)", marginTop: 5 }}>{sub}</div>
      )}
    </div>
  );
}

function SetupDStatePanel({ setupDState }: { setupDState: Record<string, SetupDEntry> }) {
  const entries = Object.entries(setupDState ?? {});
  if (entries.length === 0) return null;

  const STAGE_COLOR: Record<string, string> = {
    BOS_WAIT: "var(--warning)",
    WAIT    : "var(--accent)",
    TAPPED  : "#ff9800",
  };

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Setup-D Pipeline</span>
        <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>{entries.length} active</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr><th>Symbol</th><th>Dir</th><th>Stage</th><th>CHoCH Level</th><th>Gap Day</th><th>Sweep</th></tr>
          </thead>
          <tbody>
            {entries.map(([key, s]) => {
              const sym = key.split("|")[0] ?? key;
              const isGap = Boolean(s.is_gap_day);
              const sweep = Boolean(s.sweep_detected);
              return (
                <tr key={key}>
                  <td style={{ fontWeight: 600 }}>{sym.replace("NSE:", "")}</td>
                  <td><span className={`badge ${s.bias === "LONG" ? "badge-long" : "badge-short"}`}>{s.bias ?? "—"}</span></td>
                  <td><span style={{ color: STAGE_COLOR[s.stage ?? ""] ?? "var(--text-secondary)", fontWeight: 600, fontSize: "0.78rem" }}>{s.stage ?? "—"}</span></td>
                  <td style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>{s.choch_level != null ? s.choch_level.toFixed(2) : "—"}</td>
                  <td>
                    {isGap
                      ? <span className="badge" style={{ background: "rgba(255,152,0,0.15)", color: "#ff9800", border: "1px solid #ff9800" }}>⚡ GAP DAY</span>
                      : <span style={{ color: "var(--text-dim)", fontSize: "0.75rem" }}>—</span>
                    }
                  </td>
                  <td>
                    {sweep
                      ? <span className="badge badge-live" style={{ fontSize: "0.7rem" }}>SWEEP ✓</span>
                      : <span style={{ color: "var(--text-dim)", fontSize: "0.75rem" }}>—</span>
                    }
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ZoneStatePanel({ zoneState }: { zoneState: Record<string, unknown> }) {
  const entries = Object.entries(zoneState ?? {}) as [string, { LONG?: { zone: [number, number]; state: string; tf: string } | null; SHORT?: { zone: [number, number]; state: string; tf: string } | null }][];
  const active = entries.filter(([, v]) => v.LONG || v.SHORT);

  if (active.length === 0) return null;

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Zone State</span>
        <span style={{ marginLeft: 10, fontSize: "0.75rem", color: "var(--text-secondary)" }}>{active.length} symbols</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr><th>Symbol</th><th>Direction</th><th>Zone</th><th>State</th><th>TF</th></tr>
          </thead>
          <tbody>
            {active.flatMap(([sym, zones]) =>
              (["LONG", "SHORT"] as const).filter(d => zones[d]).map(d => {
                const z = zones[d]!;
                return (
                  <tr key={`${sym}-${d}`}>
                    <td style={{ fontWeight: 600 }}>{sym.replace("NSE:", "")}</td>
                    <td><span className={`badge ${d === "LONG" ? "badge-long" : "badge-short"}`}>{d}</span></td>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
                      {z.zone[0].toFixed(1)} – {z.zone[1].toFixed(1)}
                    </td>
                    <td>
                      <span className={`badge ${z.state === "TAPPED" ? "badge-paper" : "badge-live"}`}>{z.state}</span>
                    </td>
                    <td style={{ color: "var(--text-secondary)", fontSize: "0.78rem" }}>{z.tf}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AdaptiveIntelligencePanel({ adaptiveIntel }: { adaptiveIntel?: AdaptiveIntel }) {
  const multipliers = adaptiveIntel?.setup_multipliers ?? {};
  const blocks = adaptiveIntel?.recent_blocks ?? [];
  const scores = adaptiveIntel?.recent_ai_scores ?? [];

  return (
    <div className="glass" style={{ overflow: "hidden", border: "1px solid rgba(102,126,234,0.4)" }}>
      <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Tier 3 Intelligence</span>
        <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          {Object.keys(multipliers).length} setups tuned
        </span>
      </div>

      {Object.keys(multipliers).length === 0 && blocks.length === 0 && scores.length === 0 && (
        <div style={{ padding: "10px 20px", fontSize: "0.76rem", color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}>
          Adaptive layer is active. Waiting for fresh ranked/blocked signals to populate live insights.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4 md:px-5">
        <div>
          <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 8 }}>Adaptive Risk Multipliers</div>
          {Object.keys(multipliers).length === 0 ? (
            <div style={{ color: "var(--text-dim)", fontSize: "0.78rem" }}>No adaptive multipliers available yet</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {Object.entries(multipliers).map(([k, v]) => (
                <span key={k} className="badge badge-neutral">
                  {k}: {v.toFixed(2)}x
                </span>
              ))}
            </div>
          )}
        </div>

        <div>
          <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 8 }}>Latest AI Signal Scores</div>
          {scores.length === 0 ? (
            <div style={{ color: "var(--text-dim)", fontSize: "0.78rem" }}>No AI scores captured yet</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {scores.slice(-3).reverse().map((s, i) => (
                <div key={`${s.symbol}-${i}`} style={{ fontSize: "0.76rem", color: "var(--text-secondary)" }}>
                  <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{s.symbol?.replace("NSE:", "")}</span>
                  {" · "}
                  <span>{s.setup}</span>
                  {" · "}
                  <span style={{ color: "var(--accent)" }}>AI {s.ai_score ?? "—"}/100</span>
                  {" · "}
                  <span>{s.ts ? new Date(s.ts).toLocaleTimeString() : "—"}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--border)", padding: "12px 20px" }}>
        <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 8 }}>Recent Adaptive Blocks</div>
        {blocks.length === 0 ? (
          <div style={{ color: "var(--text-dim)", fontSize: "0.78rem" }}>No recent adaptive blocks</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {blocks.slice(-4).reverse().map((b, i) => (
              <div key={`${b.symbol}-${i}`} style={{ fontSize: "0.76rem", color: "var(--warning)" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{b.symbol?.replace("NSE:", "")}</span>
                {" · "}
                <span>{b.setup}</span>
                {" · "}
                <span>{b.reason ?? "Blocked by adaptive filter"}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
