"use client";
/**
 * /risk-engine — internal, read-only Risk Engine Dashboard.
 *
 * Summarizes the engine's daily audit logs (promotions/exits), the live book's
 * risk (heat, stop-width, sector exposure), the legacy-vs-new counterfactual,
 * the active config flags, and the config version history. No writes, no trading
 * logic — pure observability.
 */
import { useCallback, useEffect, useState } from "react";
import { api, RiskEngineSummary, RiskConfigHistoryEntry, RiskHistBucket } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const C = { good: "#00d18c", bad: "#ff4d6d", amber: "#f0c060", accent: "#22d3ee", muted: "#94a3b8" };

function lastDays(n: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(); d.setDate(d.getDate() - i);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function Stat({ label, value, color, sub }: { label: string; value: React.ReactNode; color?: string; sub?: string }) {
  return (
    <div className="glass" style={{ padding: "12px 14px", borderRadius: 12, minWidth: 130 }}>
      <div style={{ fontSize: "0.62rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: "1.35rem", fontWeight: 800, color: color ?? "var(--text-primary,#e2e8f0)" }}>{value}</div>
      {sub && <div style={{ fontSize: "0.6rem", color: C.muted }}>{sub}</div>}
    </div>
  );
}

function Bars({ data, color }: { data: RiskHistBucket[] | undefined; color: string }) {
  const max = Math.max(1, ...(data ?? []).map((b) => b.count));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {(data ?? []).map((b) => (
        <div key={b.range} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.7rem" }}>
          <span style={{ width: 54, color: C.muted, textAlign: "right" }}>{b.range}</span>
          <div style={{ flex: 1, background: "rgba(255,255,255,0.05)", borderRadius: 4, height: 14 }}>
            <div style={{ width: `${(b.count / max) * 100}%`, background: color, height: "100%", borderRadius: 4, minWidth: b.count ? 2 : 0 }} />
          </div>
          <span style={{ width: 22, color: "var(--text-primary,#e2e8f0)", fontWeight: 700 }}>{b.count}</span>
        </div>
      ))}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="glass" style={{ padding: 16, borderRadius: 12 }}>
      <div style={{ fontSize: "0.8rem", fontWeight: 800, marginBottom: 10, color: "var(--text-primary,#e2e8f0)" }}>{title}</div>
      {children}
    </div>
  );
}

export default function RiskEnginePage() {
  const { user } = useAuth();
  const [date, setDate] = useState(lastDays(1)[0]);
  const [sum, setSum] = useState<RiskEngineSummary | null>(null);
  const [hist, setHist] = useState<RiskConfigHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [s, h] = await Promise.all([api.riskSummary(date), api.riskConfigHistory(30)]);
      setSum(s); setHist(h.history ?? []);
      api.riskConfig().catch(() => {}); // triggers auto-versioning server-side
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  }, [date]);

  useEffect(() => { load(); }, [load]);

  if (!user) return <div style={{ padding: 40, textAlign: "center", color: C.muted }}>Internal dashboard — please log in.</div>;

  const p = sum?.promotions; const cf = sum?.counterfactual; const pf = sum?.portfolio;

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
        <div>
          <h1 className="text-xl font-bold" style={{ color: "var(--text-primary,#e2e8f0)" }}>⚙️ Risk Engine — Audit Dashboard</h1>
          <div style={{ fontSize: "0.68rem", color: C.muted }}>Read-only · driven from the engine&apos;s decision logs</div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <select value={date} onChange={(e) => setDate(e.target.value)}
            style={{ background: "rgba(15,23,42,0.6)", color: "var(--text-primary,#e2e8f0)", border: "1px solid rgba(148,163,184,0.2)", borderRadius: 8, padding: "6px 10px", fontSize: "0.75rem" }}>
            {lastDays(14).map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <button onClick={load} className="glass" style={{ padding: "6px 12px", borderRadius: 8, fontSize: "0.72rem", color: C.accent }}>Refresh</button>
        </div>
      </div>

      {/* config flags */}
      {sum && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
          {Object.entries(sum.flags).map(([k, v]) => (
            <span key={k} style={{ fontSize: "0.6rem", fontWeight: 700, padding: "3px 8px", borderRadius: 999,
              color: v ? C.good : C.muted, background: v ? "rgba(0,209,140,0.12)" : "rgba(148,163,184,0.1)",
              border: `1px solid ${v ? "rgba(0,209,140,0.3)" : "rgba(148,163,184,0.2)"}` }}>
              {v ? "● " : "○ "}{k}
            </span>
          ))}
        </div>
      )}

      {loading && !sum && <div style={{ padding: 40, textAlign: "center", color: C.muted }}>Loading…</div>}
      {err && <div style={{ padding: 20, textAlign: "center", color: C.bad }}>{err}</div>}

      {sum && (
        <>
          {/* top stats */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
            <Stat label="Promotions" value={p?.total ?? 0} sub={`${p?.accepted ?? 0} accepted · ${p?.rejected ?? 0} rejected`} />
            <Stat label="Stop-cap rejects" value={p?.stop_cap_rejections ?? 0} color={C.bad} />
            <Stat label="Sizing adj." value={p?.sizing_adjustments ?? 0} color={C.accent} />
            <Stat label="Liquidity adj." value={p?.liquidity_adjustments ?? 0} color={C.amber} />
            <Stat label="Trend-break exits" value={sum.exits.trend_break} color={C.bad} />
            <Stat label="Avg stop width" value={p?.avg_stop_width_pct != null ? `${p.avg_stop_width_pct}%` : "—"} />
            <Stat label="Portfolio heat" value={pf?.portfolio_heat_pct != null ? `${pf.portfolio_heat_pct}%` : "—"} color={C.amber} sub="capital at risk if all stops hit" />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))", gap: 12 }}>
            {/* counterfactual */}
            <Card title="Counterfactual — legacy vs new engine">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: "0.78rem" }}>
                <div>Legacy would accept: <b>{cf?.legacy_would_accept ?? 0}</b></div>
                <div>New accepted: <b style={{ color: C.good }}>{cf?.new_accepted ?? 0}</b></div>
                <div>Rejected by new (legacy took): <b style={{ color: C.bad }}>{cf?.rejected_by_new_that_legacy_took ?? 0}</b></div>
                <div />
                <div>Notional (legacy eq-wt): <b>₹{(cf?.notional_legacy_equal_weight ?? 0).toLocaleString("en-IN")}</b></div>
                <div>Notional (risk-wt): <b style={{ color: C.accent }}>₹{(cf?.notional_new_risk_weighted ?? 0).toLocaleString("en-IN")}</b></div>
              </div>
            </Card>

            {/* portfolio */}
            <Card title="Live book risk">
              <div style={{ fontSize: "0.78rem", marginBottom: 8 }}>
                {pf?.active_positions ?? 0} active · avg stop {pf?.avg_stop_width_pct ?? "—"}% ·
                {" "}engine-sized {pf?.engine_sized ?? 0}/{pf?.active_positions ?? 0}
              </div>
              <div style={{ fontSize: "0.62rem", color: C.muted, marginBottom: 4 }}>STOP-WIDTH DISTRIBUTION</div>
              <Bars data={pf?.stop_width_distribution} color={C.accent} />
            </Card>

            {/* accepted weight distribution */}
            <Card title="Accepted position-weight distribution (today)">
              <Bars data={p?.accepted_weight_distribution} color={C.good} />
            </Card>

            {/* sector exposure */}
            <Card title="Sector exposure (risk %)">
              {(pf?.sector_exposure ?? []).length === 0 ? <div style={{ color: C.muted, fontSize: "0.75rem" }}>No active positions.</div> :
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {(pf?.sector_exposure ?? []).map((s) => {
                    const max = Math.max(1, ...(pf?.sector_exposure ?? []).map((x) => x.risk_pct));
                    return (
                      <div key={s.sector} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.7rem" }}>
                        <span style={{ width: 70, color: C.muted }}>{s.sector}</span>
                        <div style={{ flex: 1, background: "rgba(255,255,255,0.05)", borderRadius: 4, height: 14 }}>
                          <div style={{ width: `${(s.risk_pct / max) * 100}%`, background: C.amber, height: "100%", borderRadius: 4 }} />
                        </div>
                        <span style={{ width: 40, fontWeight: 700 }}>{s.risk_pct}%</span>
                      </div>
                    );
                  })}
                </div>}
            </Card>

            {/* rejections */}
            <Card title={`Rejections (${p?.rejections?.length ?? 0})`}>
              {(p?.rejections ?? []).length === 0 ? <div style={{ color: C.muted, fontSize: "0.75rem" }}>None today.</div> :
                <div style={{ maxHeight: 180, overflowY: "auto", fontSize: "0.72rem" }}>
                  {(p?.rejections ?? []).map((r, i) => (
                    <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <span style={{ fontWeight: 700 }}>{r.symbol}</span>
                      <span style={{ color: C.muted }}>{r.stop_width_pct != null ? `${r.stop_width_pct}%` : ""}</span>
                      <span style={{ color: C.bad, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.reason}</span>
                    </div>
                  ))}
                </div>}
            </Card>

            {/* trend-break exits */}
            <Card title={`Trend-break exits (${sum.exits.trend_break})`}>
              {sum.exits.detail.length === 0 ? <div style={{ color: C.muted, fontSize: "0.75rem" }}>None today.</div> :
                <div style={{ fontSize: "0.72rem" }}>
                  {sum.exits.detail.map((e, i) => (
                    <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <span style={{ fontWeight: 700 }}>{e.symbol}</span>
                      <span style={{ color: C.muted }}>cmp {e.cmp} · 200D {e.dma200}</span>
                      <span style={{ color: C.bad }}>RS {e.rs_vs_nifty}</span>
                    </div>
                  ))}
                </div>}
            </Card>
          </div>

          {/* config history */}
          <div style={{ marginTop: 14 }}>
            <Card title="Configuration version history">
              {hist.length === 0 ? <div style={{ color: C.muted, fontSize: "0.75rem" }}>No versions recorded yet.</div> :
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", fontSize: "0.72rem", borderCollapse: "collapse" }}>
                    <thead><tr style={{ color: C.muted, textAlign: "left" }}>
                      <th style={{ padding: "4px 8px" }}>When</th><th>Source</th><th>Changes</th><th>Reason</th>
                    </tr></thead>
                    <tbody>
                      {hist.map((h) => (
                        <tr key={h.id} style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}>
                          <td style={{ padding: "4px 8px", whiteSpace: "nowrap" }}>{String(h.recorded_at).slice(0, 19).replace("T", " ")}</td>
                          <td style={{ color: h.source === "manual" ? C.accent : C.muted }}>{h.source}</td>
                          <td>{Object.entries(h.changes || {}).map(([k, v]) => (
                            <span key={k} style={{ marginRight: 8 }}>{k}: <span style={{ color: C.muted }}>{String(v[0])}</span>→<span style={{ color: C.good }}>{String(v[1])}</span></span>
                          )) || "—"}</td>
                          <td style={{ color: C.muted }}>{h.reason || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
