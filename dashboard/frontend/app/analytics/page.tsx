"use client";
/**
 * /analytics — Full Algo Performance Dashboard
 * Hero bar → Intraday section → Swing section → Long-Term section
 */
import { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Cell, PieChart, Pie, Legend,
} from "recharts";
import {
  api,
  AnalyticsSummary, EquityPoint, SetupStat, RollingWRPoint,
  ResearchPerformanceResponse, ResearchPickRow,
} from "@/lib/api";
import { pnlColor, StatusBadge } from "@/components/StatusBadge";

// ── Helpers ────────────────────────────────────────────────────────────────────

function useChartHeight() {
  const [h, setH] = useState(220);
  useEffect(() => {
    const update = () => setH(typeof window !== "undefined" && window.innerWidth < 768 ? 180 : 220);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return h;
}

const fmt = {
  pct:  (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`,
  r:    (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`,
  date: (s: string) => s ? s.slice(0, 10) : "—",
};

// ── Hero Stats Bar ─────────────────────────────────────────────────────────────

interface HeroProps {
  intraday: AnalyticsSummary | null;
  swing: ResearchPerformanceResponse | null;
  longterm: ResearchPerformanceResponse | null;
}

function HeroBar({ intraday, swing, longterm }: HeroProps) {
  const totalR  = intraday?.total_r ?? 0;
  const wr      = ((intraday?.win_rate ?? 0) * 100);
  const swingHR = swing?.summary.hit_rate_pct ?? 0;
  const swingAvg= swing?.summary.avg_pnl_pct ?? 0;
  const ltHR    = longterm?.summary.hit_rate_pct ?? 0;
  const ltAvg   = longterm?.summary.avg_pnl_pct ?? 0;

  // Composite algo score: weighted avg of WR + hit rates (all out of 100)
  const score = Math.round((wr * 0.4 + swingHR * 0.35 + ltHR * 0.25));

  const cards = [
    {
      label: "Intraday",
      sub: "Total R · Win Rate",
      val1: fmt.r(totalR),
      val2: `${wr.toFixed(1)}% WR`,
      col1: pnlColor(totalR),
      col2: wr >= 50 ? "var(--success)" : "var(--warning)",
      icon: "⚡",
    },
    {
      label: "Swing Picks",
      sub: "Hit Rate · Avg Gain",
      val1: `${swingHR.toFixed(1)}%`,
      val2: fmt.pct(swingAvg),
      col1: swingHR >= 50 ? "var(--success)" : "var(--warning)",
      col2: pnlColor(swingAvg),
      icon: "📈",
    },
    {
      label: "Long-Term",
      sub: "Hit Rate · Avg Gain",
      val1: `${ltHR.toFixed(1)}%`,
      val2: fmt.pct(ltAvg),
      col1: ltHR >= 50 ? "var(--success)" : "var(--warning)",
      col2: pnlColor(ltAvg),
      icon: "🏦",
    },
    {
      label: "Algo Score",
      sub: "Composite Performance",
      val1: `${score}/100`,
      val2: score >= 60 ? "Strong" : score >= 40 ? "Average" : "Weak",
      col1: score >= 60 ? "var(--success)" : score >= 40 ? "var(--warning)" : "var(--danger)",
      col2: score >= 60 ? "var(--success)" : score >= 40 ? "var(--warning)" : "var(--danger)",
      icon: "🏆",
    },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14 }}>
      {cards.map((c) => (
        <div key={c.label} className="glass" style={{
          padding: "18px 20px",
          borderRadius: 12,
          borderLeft: `3px solid ${c.col1}`,
          display: "flex", flexDirection: "column", gap: 6,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: ".07em" }}>{c.label}</span>
            <span style={{ fontSize: "1.2rem" }}>{c.icon}</span>
          </div>
          <div style={{ fontSize: "1.55rem", fontWeight: 800, color: c.col1 }}>{c.val1}</div>
          <div style={{ fontSize: "0.88rem", fontWeight: 600, color: c.col2 }}>{c.val2}</div>
          <div style={{ fontSize: "0.65rem", color: "var(--text-dim)" }}>{c.sub}</div>
        </div>
      ))}
    </div>
  );
}

// ── Research section (Swing / Long-Term) ────────────────────────────────────────

function ResearchSection({ data, label, color }: { data: ResearchPerformanceResponse | null; label: string; color: string }) {
  if (!data) return null;
  const { summary, picks } = data;

  const donutData = [
    { name: "Target Hit", value: summary.target_hit },
    { name: "Stop Hit",   value: summary.stop_hit },
    { name: "Active",     value: summary.active },
    { name: "Pending",    value: summary.total - summary.target_hit - summary.stop_hit - summary.active },
  ].filter(d => d.value > 0);

  const DONUT_COLORS = ["#00d18c", "#ff4d4d", "#5b9cf6", "#888"];

  // Build bar data: top 10 picks by P&L%
  const sorted = [...picks].sort((a, b) => b.profit_loss_pct - a.profit_loss_pct).slice(0, 10);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Section header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 4, height: 28, borderRadius: 4, background: color }} />
        <h2 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>{label}</h2>
        <span style={{
          padding: "2px 10px", borderRadius: 20, fontSize: "0.7rem", fontWeight: 600,
          background: `${color}22`, color, border: `1px solid ${color}44`,
        }}>{summary.total} picks · {summary.hit_rate_pct.toFixed(1)}% hit rate</span>
      </div>

      {/* Summary stat pills */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
        {[
          { l: "Active",      v: summary.active,           c: "#5b9cf6" },
          { l: "Target Hit",  v: summary.target_hit,       c: "#00d18c" },
          { l: "Stop Hit",    v: summary.stop_hit,         c: "#ff4d4d" },
          { l: "Hit Rate",    v: `${summary.hit_rate_pct.toFixed(1)}%`, c: summary.hit_rate_pct >= 50 ? "#00d18c" : "#ffc800" },
          { l: "Avg P&L",     v: fmt.pct(summary.avg_pnl_pct), c: pnlColor(summary.avg_pnl_pct) },
          { l: "Best Pick",   v: summary.best_symbol ?? "—",  c: "#00d18c" },
          { l: "Worst Pick",  v: summary.worst_symbol ?? "—", c: "#ff4d4d" },
        ].map(({ l, v, c }) => (
          <div className="stat-card" key={l} style={{ padding: "12px 14px" }}>
            <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", letterSpacing: ".07em", marginBottom: 4 }}>{l.toUpperCase()}</div>
            <div style={{ fontSize: "1.15rem", fontWeight: 700, color: c }}>{v}</div>
          </div>
        ))}
      </div>

      {/* Charts row: donut + bar */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="glass" style={{ padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 12, fontSize: "0.9rem" }}>Status Breakdown</div>
          {donutData.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie data={donutData} cx="50%" cy="50%" innerRadius={48} outerRadius={72}
                  paddingAngle={3} dataKey="value">
                  {donutData.map((_, i) => <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
                <Legend iconSize={10} wrapperStyle={{ fontSize: "0.72rem" }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ textAlign: "center", color: "var(--text-dim)", paddingTop: 60, fontSize: "0.85rem" }}>No data yet</div>
          )}
        </div>

        <div className="glass" style={{ padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 12, fontSize: "0.9rem" }}>Top 10 Picks by P&L%</div>
          {sorted.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={sorted} layout="vertical" margin={{ left: 10, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
                <XAxis type="number" tick={{ fill: "var(--text-dim)", fontSize: 10 }}
                  tickFormatter={(v) => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`} />
                <YAxis type="category" dataKey="symbol" tick={{ fill: "var(--text-dim)", fontSize: 10 }} width={90} />
                <Tooltip contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`, "P&L"]} />
                <Bar dataKey="profit_loss_pct" radius={[0, 4, 4, 0]}>
                  {sorted.map((r, i) => (
                    <Cell key={i} fill={r.profit_loss_pct >= 0 ? "#00d18c" : "#ff4d4d"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ textAlign: "center", color: "var(--text-dim)", paddingTop: 60, fontSize: "0.85rem" }}>No data yet</div>
          )}
        </div>
      </div>

      {/* Per-symbol table */}
      <div className="glass" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", fontWeight: 600, fontSize: "0.85rem" }}>
          All Picks
        </div>
        <div style={{ overflowX: "auto", maxHeight: 380 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th><th>Symbol</th><th>Setup</th><th>Entry ₹</th>
                <th>CMP ₹</th><th>P&L%</th><th>Days</th><th>Status</th><th>Recommended</th>
              </tr>
            </thead>
            <tbody>
              {picks.length === 0 ? (
                <tr><td colSpan={9} style={{ textAlign: "center", color: "var(--text-dim)", padding: 24 }}>No picks yet — run a scan first</td></tr>
              ) : (
                picks.map((p: ResearchPickRow, i: number) => (
                  <tr key={`${p.symbol}-${i}`}>
                    <td style={{ color: "var(--text-dim)" }}>{i + 1}</td>
                    <td style={{ fontWeight: 700, color: "var(--text-primary)" }}>{p.symbol}</td>
                    <td style={{ color: "var(--text-secondary)", fontSize: "0.8rem" }}>{p.setup ?? "—"}</td>
                    <td style={{ fontFamily: "monospace" }}>₹{p.entry_price?.toFixed(2)}</td>
                    <td style={{ fontFamily: "monospace" }}>
                      {p.current_price ? `₹${p.current_price.toFixed(2)}` : "—"}
                    </td>
                    <td style={{ fontFamily: "monospace", fontWeight: 700, color: pnlColor(p.profit_loss_pct) }}>
                      {fmt.pct(p.profit_loss_pct)}
                    </td>
                    <td>{p.days_held}</td>
                    <td><StatusBadge status={p.status} /></td>
                    <td style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                      {fmt.date(p.recommended_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

// ── Section divider ────────────────────────────────────────────────────────────

function SectionDivider({ label }: { label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "8px 0" }}>
      <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
      <span style={{
        fontSize: "0.7rem", fontWeight: 700, letterSpacing: ".12em",
        color: "var(--text-dim)", textTransform: "uppercase", whiteSpace: "nowrap",
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [summary,    setSummary   ] = useState<AnalyticsSummary | null>(null);
  const [equity,     setEquity    ] = useState<EquityPoint[]>([]);
  const [setups,     setSetups    ] = useState<SetupStat[]>([]);
  const [rolling,    setRolling   ] = useState<RollingWRPoint[]>([]);
  const [swingPerf,  setSwingPerf ] = useState<ResearchPerformanceResponse | null>(null);
  const [ltPerf,     setLtPerf   ] = useState<ResearchPerformanceResponse | null>(null);
  const [loading,    setLoading   ] = useState(true);
  const [syncInfo,   setSyncInfo  ] = useState<{ csv_exists?: boolean; db_trade_count?: number } | null>(null);
  const chartHeight = useChartHeight();

  const load = useCallback(() => {
    Promise.all([
      api.summary(), api.equityCurve(), api.bySetup(), api.rollingWR(20),
      api.syncStatus(), api.swingPerformance(), api.longtermPerformance(),
    ])
      .then(([s, e, b, r, sync, sp, lp]) => {
        setSummary(s);
        setEquity(e.equity_curve ?? []);
        setSetups(b.setups ?? []);
        setRolling(r.data ?? []);
        setSyncInfo(sync as Record<string, unknown>);
        setSwingPerf(sp as ResearchPerformanceResponse);
        setLtPerf(lp as ResearchPerformanceResponse);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") load();
    }, 30_000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return <Loader />;

  const totalR = summary?.total_r ?? 0;
  const wr     = ((summary?.win_rate ?? 0) * 100).toFixed(1);
  const pf     = (summary?.profit_factor ?? 0).toFixed(2);
  const exp    = (summary?.expectancy_r ?? 0).toFixed(3);
  const maxDD  = (summary?.max_drawdown_r ?? 0).toFixed(2);
  const maxCL  = summary?.max_consec_losses ?? 0;
  const total  = summary?.total_trades ?? 0;

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Page header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <h1 className="text-xl md:text-2xl lg:text-3xl font-bold m-0" style={{ color: "var(--text-primary)" }}>Analytics</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
            Full algo performance — intraday · swing picks · long-term ideas
          </p>
        </div>
        {syncInfo && (
          <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)", alignSelf: "center" }}>
            DB: {syncInfo.db_trade_count ?? 0} intraday trades
            {syncInfo.csv_exists ? " · CSV synced" : " · No CSV"}
          </span>
        )}
      </div>

      {/* ── HERO BAR ───────────────────────────────────────────────────── */}
      <HeroBar intraday={summary} swing={swingPerf} longterm={ltPerf} />

      {/* ── SECTION 1: INTRADAY ─────────────────────────────────────────── */}
      <SectionDivider label="Intraday Trading" />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 12 }}>
        {[
          { label: "Total R",        value: `${totalR >= 0 ? "+" : ""}${totalR.toFixed(2)}R`, color: pnlColor(totalR) },
          { label: "Win Rate",       value: `${wr}%`,     color: parseFloat(wr) >= 50 ? "var(--success)" : "var(--warning)" },
          { label: "Profit Factor",  value: pf,           color: parseFloat(pf) >= 1  ? "var(--success)" : "var(--danger)" },
          { label: "Expectancy",     value: `${exp}R`,    color: parseFloat(exp) >= 0 ? "var(--success)" : "var(--danger)" },
          { label: "Max Drawdown",   value: `${maxDD}R`,  color: "var(--danger)" },
          { label: "Max Consec Loss",value: String(maxCL),color: maxCL >= 5 ? "var(--danger)" : "var(--warning)" },
          { label: "Total Trades",   value: String(total),color: "var(--text-primary)" },
        ].map(({ label, value, color }) => (
          <div className="stat-card" key={label}>
            <div style={{ fontSize: "0.67rem", color: "var(--text-secondary)", letterSpacing: ".07em", marginBottom: 5 }}>{label.toUpperCase()}</div>
            <div style={{ fontSize: "1.3rem", fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="glass w-full overflow-hidden" style={{ padding: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 16, fontSize: "0.9rem" }}>Equity Curve (Cumulative R)</div>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <LineChart data={equity} margin={{ left: 0, right: 10, top: 5, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="date" tick={{ fill: "var(--text-dim)", fontSize: 10 }}
              tickFormatter={(v) => v?.slice(5, 10)} interval={Math.max(1, Math.floor(equity.length / 6))} />
            <YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} tickFormatter={(v) => `${v}R`} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
            <Tooltip
              contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: "var(--text-secondary)" }}
              formatter={(v) => [`${Number(v).toFixed(2)}R`, "Cumulative"]}
            />
            <Line type="monotone" dataKey="cumulative_r" stroke="var(--accent)" strokeWidth={2} dot={false}
              activeDot={{ r: 4, fill: "var(--accent)" }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="glass w-full overflow-hidden" style={{ padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 16, fontSize: "0.9rem" }}>Setup Performance (Total R)</div>
          <ResponsiveContainer width="100%" height={Math.min(chartHeight, 200)}>
            <BarChart data={setups} margin={{ left: 0, right: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="setup" tick={{ fill: "var(--text-dim)", fontSize: 9 }}
                tickFormatter={(v) => v.replace("UNIVERSAL-", "UNI-").replace("HIERARCHICAL", "HIER")} />
              <YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} tickFormatter={(v) => `${v}R`} />
              <Tooltip
                contentStyle={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${Number(v).toFixed(2)}R`, "Total R"]}
              />
              <Bar dataKey="total_r" fill="var(--accent)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="glass w-full overflow-hidden" style={{ padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 16, fontSize: "0.9rem" }}>Rolling Win Rate (20 trades)</div>
          <ResponsiveContainer width="100%" height={Math.min(chartHeight, 200)}>
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

      {/* ── SECTION 2: SWING SCAN ─────────────────────────────────────────── */}
      <SectionDivider label="Swing Scan Performance" />
      <ResearchSection data={swingPerf} label="Swing Scan Recommendations" color="#5b9cf6" />

      {/* ── SECTION 3: LONG-TERM ──────────────────────────────────────────── */}
      <SectionDivider label="Long-Term Investment Performance" />
      <ResearchSection data={ltPerf} label="Long-Term Investment Recommendations" color="#a78bfa" />
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
