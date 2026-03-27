"use client";
/**
 * /market-intelligence — Market Intelligence Dashboard
 * Aggregates holiday calendar, FX rates, US macro (FRED), MF flows, and QuickChart.
 */
import { useEffect, useState, useCallback } from "react";
import {
  Globe, Calendar, DollarSign, TrendingUp, BarChart3,
  RefreshCw, AlertTriangle, Sun, ArrowUpRight, ArrowDownRight,
} from "lucide-react";
import {
  api,
  MISnapshot,
  MIHoliday,
} from "@/lib/api";

// ── Helpers ────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(decimals);
}

function chgColor(v: number): string {
  return v >= 0 ? "var(--success, #00d18c)" : "var(--danger, #ff4d4d)";
}

function chgArrow(v: number) {
  return v >= 0
    ? <ArrowUpRight size={14} style={{ color: "var(--success, #00d18c)" }} />
    : <ArrowDownRight size={14} style={{ color: "var(--danger, #ff4d4d)" }} />;
}

// ── Card wrapper ───────────────────────────────────────────────────────────

function Card({ title, icon: Icon, children, span = 1 }: {
  title: string;
  icon: React.ComponentType<{ size?: number }>;
  children: React.ReactNode;
  span?: number;
}) {
  return (
    <div
      className={`rounded-xl border p-5 ${span === 2 ? "md:col-span-2" : ""}`}
      style={{
        background: "var(--card-bg, rgba(15,23,42,0.6))",
        borderColor: "var(--border, rgba(0,212,255,0.08))",
        backdropFilter: "blur(12px)",
      }}
    >
      <div className="flex items-center gap-2 mb-4">
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center"
          style={{
            background: "rgba(0,212,255,0.1)",
            border: "1px solid rgba(0,212,255,0.2)",
          }}
        >
          <Icon size={14} />
        </div>
        <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary, #e2e8f0)" }}>
          {title}
        </h3>
      </div>
      {children}
    </div>
  );
}

// ── Stat pill ──────────────────────────────────────────────────────────────

function Stat({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[0.65rem] uppercase tracking-wider"
        style={{ color: "var(--text-dim, #64748b)" }}>{label}</span>
      <span className="text-lg font-bold" style={{ color: color || "var(--text-primary, #e2e8f0)" }}>
        {value}
      </span>
      {sub && <span className="text-[0.7rem]" style={{ color: "var(--text-secondary, #94a3b8)" }}>{sub}</span>}
    </div>
  );
}

// ── Holiday row ────────────────────────────────────────────────────────────

function HolidayRow({ h, isNext }: { h: MIHoliday; isNext: boolean }) {
  const d = new Date(h.date);
  const dayName = d.toLocaleDateString("en-IN", { weekday: "short" });
  return (
    <div
      className="flex items-center justify-between py-2 px-3 rounded-lg"
      style={{
        background: isNext ? "rgba(0,212,255,0.06)" : "transparent",
        borderLeft: isNext ? "2px solid var(--accent, #00d4ff)" : "2px solid transparent",
      }}
    >
      <div className="flex items-center gap-2">
        <Calendar size={13} style={{ color: "var(--text-dim, #64748b)" }} />
        <span className="text-sm" style={{ color: "var(--text-primary, #e2e8f0)" }}>{h.name}</span>
      </div>
      <span className="text-xs" style={{ color: "var(--text-secondary, #94a3b8)" }}>
        {h.date} ({dayName})
      </span>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────

export default function MarketIntelligencePage() {
  const [data, setData] = useState<MISnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const snap = await api.marketIntelSnapshot();
      setData(snap);
      setError(null);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    // Refresh every 5 minutes
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <RefreshCw size={24} className="animate-spin" style={{ color: "var(--accent, #00d4ff)" }} />
      </div>
    );
  }

  const holidays = data?.holidays || [];
  const nextHoliday = data?.next_holiday;
  const fx = data?.fx;
  const macro = data?.macro;
  const mfFlows = data?.mf_flows;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{
              background: "linear-gradient(135deg, rgba(0,212,255,0.15), rgba(139,92,246,0.15))",
              border: "1px solid rgba(0,212,255,0.2)",
            }}
          >
            <Globe size={20} style={{ color: "var(--accent, #00d4ff)" }} />
          </div>
          <div>
            <h1 className="text-xl font-bold" style={{ color: "var(--text-primary, #e2e8f0)" }}>
              Market Intelligence
            </h1>
            <p className="text-xs" style={{ color: "var(--text-secondary, #94a3b8)" }}>
              Holidays · FX · US Macro · MF Flows
            </p>
          </div>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
          style={{
            background: "rgba(0,212,255,0.1)",
            border: "1px solid rgba(0,212,255,0.2)",
            color: "var(--accent, #00d4ff)",
            opacity: loading ? 0.5 : 1,
          }}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg"
          style={{ background: "rgba(255,77,77,0.1)", border: "1px solid rgba(255,77,77,0.2)" }}>
          <AlertTriangle size={14} style={{ color: "#ff4d4d" }} />
          <span className="text-sm" style={{ color: "#ff4d4d" }}>{error}</span>
        </div>
      )}

      {/* Holiday Today Alert */}
      {data?.is_holiday_today && (
        <div className="flex items-center gap-2 p-4 rounded-xl"
          style={{
            background: "linear-gradient(135deg, rgba(255,200,0,0.1), rgba(255,150,0,0.1))",
            border: "1px solid rgba(255,200,0,0.3)",
          }}>
          <Sun size={18} style={{ color: "#ffc800" }} />
          <span className="text-sm font-semibold" style={{ color: "#ffc800" }}>
            Today is a Public Holiday — NSE is closed
          </span>
        </div>
      )}

      {/* Main Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">

        {/* ── FX Card ────────────────────────────────── */}
        <Card title="USD / INR" icon={DollarSign}>
          <div className="flex items-end gap-4">
            <Stat
              label="Exchange Rate"
              value={fx?.usd_inr ? `₹${fmt(fx.usd_inr, 4)}` : "—"}
              color="var(--accent, #00d4ff)"
            />
            {fx?.chg_pct !== undefined && fx.chg_pct !== 0 && (
              <div className="flex items-center gap-1 pb-1">
                {chgArrow(fx.chg_pct)}
                <span className="text-sm font-medium" style={{ color: chgColor(fx.chg_pct) }}>
                  {fx.chg_pct >= 0 ? "+" : ""}{fmt(fx.chg_pct)}%
                </span>
              </div>
            )}
          </div>
          <div className="mt-3 text-[0.65rem]" style={{ color: "var(--text-dim, #64748b)" }}>
            Source: {fx?.source || "frankfurter.dev"} · {fx?.fetched_at ? new Date(fx.fetched_at).toLocaleTimeString() : ""}
          </div>
        </Card>

        {/* ── US Macro (FRED) ────────────────────────── */}
        <Card title="US Macro Indicators" icon={TrendingUp}>
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Fed Funds Rate" value={macro?.fed_funds_rate != null ? `${fmt(macro.fed_funds_rate)}%` : "—"} />
            <Stat label="US 10Y Yield" value={macro?.us_10y_yield != null ? `${fmt(macro.us_10y_yield)}%` : "—"} />
            <Stat label="DXY Index" value={macro?.dxy_index != null ? fmt(macro.dxy_index) : "—"} />
            <Stat label="US CPI (Index)" value={macro?.us_cpi_yoy != null ? fmt(macro.us_cpi_yoy, 1) : "—"} />
          </div>
          <div className="mt-3 text-[0.65rem]" style={{ color: "var(--text-dim, #64748b)" }}>
            Source: FRED · {macro?.fetched_at ? new Date(macro.fetched_at).toLocaleTimeString() : "No API key set"}
          </div>
        </Card>

        {/* ── Next Holiday Card ──────────────────────── */}
        <Card title="Next Holiday" icon={Calendar}>
          {nextHoliday ? (
            <div>
              <div className="text-lg font-bold" style={{ color: "var(--accent, #00d4ff)" }}>
                {nextHoliday.name}
              </div>
              <div className="text-sm mt-1" style={{ color: "var(--text-secondary, #94a3b8)" }}>
                {nextHoliday.date} ({new Date(nextHoliday.date).toLocaleDateString("en-IN", { weekday: "long" })})
              </div>
              <div className="text-xs mt-2" style={{ color: "var(--text-dim, #64748b)" }}>
                {Math.ceil((new Date(nextHoliday.date).getTime() - Date.now()) / 86400000)} days away
              </div>
            </div>
          ) : (
            <span className="text-sm" style={{ color: "var(--text-dim, #64748b)" }}>No upcoming holidays found</span>
          )}
        </Card>

        {/* ── MF Flows (full width) ──────────────────── */}
        <Card title="Mutual Fund NAV Tracker" icon={BarChart3} span={2}>
          {mfFlows?.top_equity_funds && mfFlows.top_equity_funds.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ color: "var(--text-dim, #64748b)" }}>
                    <th className="text-left py-2 text-xs font-medium">Fund</th>
                    <th className="text-right py-2 text-xs font-medium">NAV</th>
                    <th className="text-right py-2 text-xs font-medium">Change</th>
                    <th className="text-right py-2 text-xs font-medium">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {mfFlows.top_equity_funds.map((f, i) => (
                    <tr key={i} className="border-t" style={{ borderColor: "rgba(0,212,255,0.06)" }}>
                      <td className="py-2 pr-3" style={{ color: "var(--text-primary, #e2e8f0)" }}>
                        <div className="font-medium text-xs">{f.scheme_name}</div>
                        <div className="text-[0.6rem]" style={{ color: "var(--text-dim, #64748b)" }}>{f.fund_house}</div>
                      </td>
                      <td className="text-right py-2 font-mono text-xs" style={{ color: "var(--text-primary, #e2e8f0)" }}>
                        ₹{fmt(f.nav)}
                      </td>
                      <td className="text-right py-2">
                        <span className="inline-flex items-center gap-1 text-xs font-medium"
                          style={{ color: chgColor(f.chg_pct) }}>
                          {chgArrow(f.chg_pct)}
                          {f.chg_pct >= 0 ? "+" : ""}{fmt(f.chg_pct)}%
                        </span>
                      </td>
                      <td className="text-right py-2 text-xs" style={{ color: "var(--text-secondary, #94a3b8)" }}>
                        {f.nav_date}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <span className="text-sm" style={{ color: "var(--text-dim, #64748b)" }}>No MF data available</span>
          )}
          <div className="mt-2 text-[0.65rem]" style={{ color: "var(--text-dim, #64748b)" }}>
            Source: mfapi.in · {mfFlows?.fetched_at ? new Date(mfFlows.fetched_at).toLocaleTimeString() : ""}
          </div>
        </Card>

        {/* ── Holiday Calendar ───────────────────────── */}
        <Card title={`Holiday Calendar ${new Date().getFullYear()}`} icon={Calendar}>
          <div className="space-y-1 max-h-[300px] overflow-y-auto pr-1">
            {holidays.length > 0 ? holidays.map((h, i) => (
              <HolidayRow key={i} h={h} isNext={nextHoliday?.date === h.date} />
            )) : (
              <span className="text-sm" style={{ color: "var(--text-dim, #64748b)" }}>No holidays loaded</span>
            )}
          </div>
        </Card>
      </div>

      {/* Footer */}
      <div className="text-center text-[0.65rem] py-2" style={{ color: "var(--text-dim, #64748b)" }}>
        Data from NSE · Frankfurter · FRED · mfapi.in
        {data?.fetched_at && ` · Last updated: ${new Date(data.fetched_at).toLocaleString()}`}
      </div>
    </div>
  );
}
