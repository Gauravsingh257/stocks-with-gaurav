"use client";
/**
 * /analytics — Performance Analytics Page
 */
import { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine
} from "recharts";
import { api, AnalyticsSummary, EquityPoint, SetupStat, RollingWRPoint } from "@/lib/api";

export default function AnalyticsPage() {
  const [summary,    setSummary   ] = useState<AnalyticsSummary | null>(null);
  const [equity,     setEquity    ] = useState<EquityPoint[]>([]);
  const [setups,     setSetups    ] = useState<SetupStat[]>([]);
  const [rolling,    setRolling   ] = useState<RollingWRPoint[]>([]);
  const [loading,    setLoading   ] = useState(true);
  const [dataSource, setDataSource] = useState<string>("—");
  const [syncInfo,   setSyncInfo  ] = useState<{ csv_exists?: boolean; db_trade_count?: number; last_sync?: string } | null>(null);

  const load = useCallback(() => {
    Promise.all([api.summary(), api.equityCurve(), api.bySetup(), api.rollingWR(20), api.syncStatus()])
      .then(([s, e, b, r, sync]) => {
        setSummary(s);
        setEquity(e.equity_curve ?? []);
        setSetups(b.setups ?? []);
        setRolling(r.data ?? []);
        // data_source may come from summary or equity (both carry it now)
        setDataSource((s as Record<string, unknown>)["data_source"] as string ?? "trades");
        setSyncInfo(sync as Record<string, unknown>);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  // On mount: trigger a force-sync to pick up any new CSV data, then load
  useEffect(() => {
    api.forceSync().catch(() => {/* silent: CSV may not exist */}).finally(() => {
      load();
    });
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return <Loader />;

  const pnlColor   = (v: number) => v >= 0 ? "var(--success)" : "var(--danger)";
  const totalR     = summary?.total_r ?? 0;
  const wr         = ((summary?.win_rate ?? 0) * 100).toFixed(1);
  const pf         = (summary?.profit_factor ?? 0).toFixed(2);
  const exp        = (summary?.expectancy_r ?? 0).toFixed(3);
  const maxDD      = (summary?.max_drawdown_r ?? 0).toFixed(2);
  const maxCL      = summary?.max_consec_losses ?? 0;
  const total      = summary?.total_trades ?? 0;

  const sourceLabel = dataSource === "signal_log" ? "Live Signal Log" : "Trade Ledger (CSV)";
  const sourceBadgeColor = dataSource === "signal_log" ? "#00d18c" : "#5b9cf6";

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>Analytics</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
            Performance metrics from {total} historical trades
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            padding: "3px 10px", borderRadius: 20, fontSize: "0.72rem", fontWeight: 600,
            background: `${sourceBadgeColor}22`, border: `1px solid ${sourceBadgeColor}55`,
            color: sourceBadgeColor,
          }}>
            {sourceLabel}
          </span>
          {syncInfo && (
            <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>
              DB: {syncInfo.db_trade_count ?? 0} trades
              {syncInfo.csv_exists ? " · CSV synced" : " · No CSV (using signal log)"}
            </span>
          )}
        </div>
      </div>

      {/* Stat row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
        {[
          { label: "Total R",        value: `${totalR >= 0 ? "+" : ""}${totalR.toFixed(2)}R`, color: pnlColor(totalR) },
          { label: "Win Rate",       value: `${wr}%`,     color: parseFloat(wr) >= 50 ? "var(--success)" : "var(--warning)" },
          { label: "Profit Factor",  value: pf,           color: parseFloat(pf) >= 1 ? "var(--success)" : "var(--danger)" },
          { label: "Expectancy",     value: `${exp}R`,    color: parseFloat(exp) >= 0 ? "var(--success)" : "var(--danger)" },
          { label: "Max Drawdown",   value: `${maxDD}R`,  color: "var(--danger)" },
          { label: "Max Consec Loss",value: String(maxCL),color: maxCL >= 5 ? "var(--danger)" : "var(--warning)" },
          { label: "Total Trades",   value: String(total),color: "var(--text-primary)" },
        ].map(({ label, value, color }) => (
          <div className="stat-card" key={label}>
            <div style={{ fontSize: "0.67rem", color: "var(--text-secondary)", letterSpacing: ".07em", marginBottom: 5 }}>{label.toUpperCase()}</div>
            <div style={{ fontSize: "1.35rem", fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="glass" style={{ padding: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 16, fontSize: "0.9rem" }}>Equity Curve (Cumulative R)</div>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={equity} margin={{ left: 0, right: 10, top: 5, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="date" tick={{ fill: "var(--text-dim)", fontSize: 10 }}
              tickFormatter={(v) => v?.slice(5, 10)} interval={Math.floor(equity.length / 6)} />
            <YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} tickFormatter={(v) => `${v}R`} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
            <Tooltip
              contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: "var(--text-secondary)" }}
              formatter={(v) => [`${Number(v).toFixed(2)}R`, "Cumulative"]}
            />
            <Line type="monotone" dataKey="cumulative_r"
              stroke="var(--accent)" strokeWidth={2} dot={false}
              activeDot={{ r: 4, fill: "var(--accent)" }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* Setup breakdown */}
        <div className="glass" style={{ padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 16, fontSize: "0.9rem" }}>Setup Performance (Total R)</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={setups} margin={{ left: 0, right: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="setup" tick={{ fill: "var(--text-dim)", fontSize: 9 }}
                tickFormatter={(v) => v.replace("UNIVERSAL-", "UNI-").replace("HIERARCHICAL", "HIER")} />
              <YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} tickFormatter={(v) => `${v}R`} />
              <Tooltip
                contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${Number(v).toFixed(2)}R`, "Total R"]}
              />
              <Bar dataKey="total_r" fill="var(--accent)" radius={[3, 3, 0, 0]}
                // color positive green, negative red
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Rolling win rate */}
        <div className="glass" style={{ padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 16, fontSize: "0.9rem" }}>Rolling Win Rate (20 trades)</div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={rolling} margin={{ left: 0, right: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="idx" tick={{ fill: "var(--text-dim)", fontSize: 10 }} />
              <YAxis domain={[0, 1]} tick={{ fill: "var(--text-dim)", fontSize: 10 }}
                tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
              <ReferenceLine y={0.5} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 4" />
              <Tooltip
                contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${(Number(v) * 100).toFixed(1)}%`, "Win Rate"]}
              />
              <Line type="monotone" dataKey="win_rate" stroke="var(--success)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Setup table */}
      <div className="glass" style={{ overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", fontWeight: 600, fontSize: "0.9rem" }}>
          Setup Breakdown
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr><th>Setup</th><th>Trades</th><th>W/L</th><th>Win Rate</th><th>Total R</th><th>Expectancy</th></tr>
            </thead>
            <tbody>
              {setups.map((s) => (
                <tr key={s.setup}>
                  <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>{s.setup}</td>
                  <td>{s.total}</td>
                  <td style={{ color: "var(--text-secondary)" }}>{s.wins}/{s.total - s.wins}</td>
                  <td>
                    <span style={{ color: s.win_rate >= 0.5 ? "var(--success)" : "var(--danger)" }}>
                      {(s.win_rate * 100).toFixed(1)}%
                    </span>
                  </td>
                  <td style={{ fontFamily: "monospace", color: s.total_r >= 0 ? "var(--success)" : "var(--danger)" }}>
                    {s.total_r >= 0 ? "+" : ""}{s.total_r.toFixed(2)}R
                  </td>
                  <td style={{ fontFamily: "monospace", color: s.expectancy_r >= 0 ? "var(--success)" : "var(--danger)" }}>
                    {s.expectancy_r >= 0 ? "+" : ""}{s.expectancy_r.toFixed(3)}R
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Loader() {
  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 80 }}>
      <div style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>Loading analytics…</div>
    </div>
  );
}
