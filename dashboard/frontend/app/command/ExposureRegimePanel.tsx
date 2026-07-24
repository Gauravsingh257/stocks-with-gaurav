"use client";

/**
 * ExposureRegimePanel — PR2 (Regime Governor / Sector Leadership).
 *
 * The "first thing users see" on the Command Center: current market regime,
 * suggested exposure vs cash %, and leading / lagging sectors. Reads the
 * on-demand /api/market/state endpoint (informational even when enforcement is
 * off). Fails silently — never blocks the page.
 */

import { useEffect, useState } from "react";
import { Shield, TrendingUp, TrendingDown } from "lucide-react";
import { api, type MarketStateResponse } from "@/lib/api";

const TONE: Record<string, string> = {
  "🟢 Aggressive": "#22c55e",
  "🟢 Normal": "#22c55e",
  "🟡 Defensive": "#eab308",
  "🔴 Risk-Off": "#ef4444",
};

const STATE_LABEL: Record<string, string> = {
  STRONG_BULL: "Strong Bull",
  WEAK_BULL: "Weak Bull",
  SIDEWAYS: "Sideways",
  CORRECTION: "Correction",
  BEAR: "Bear",
  UNKNOWN: "Unknown",
};

function Meter({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{ height: 8, borderRadius: 6, background: "rgba(148,163,184,0.18)", overflow: "hidden" }}>
      <div style={{ width: `${Math.max(0, Math.min(100, pct))}%`, height: "100%", background: color, transition: "width .4s" }} />
    </div>
  );
}

export function ExposureRegimePanel({ ideaCount }: { ideaCount?: number }) {
  const [s, setS] = useState<MarketStateResponse | null>(null);

  useEffect(() => {
    let alive = true;
    api.marketState().then((r) => alive && setS(r)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!s) return null;
  const color = TONE[s.exposure_label] ?? "#22c55e";
  const defensive = s.exposure_pct < 60;

  return (
    <div className="glass rounded-xl" style={{ padding: "16px 18px", border: `1px solid ${color}55` }}>
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          {defensive ? <Shield size={17} color={color} /> : <TrendingUp size={17} color={color} />}
          <div>
            <div style={{ fontSize: "0.62rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: 0.6 }}>
              Market Regime
            </div>
            <div style={{ fontWeight: 800, fontSize: "1.02rem", color }}>
              {s.exposure_label} · {STATE_LABEL[s.market_state] ?? s.market_state}
            </div>
          </div>
        </div>
        {typeof ideaCount === "number" && (
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "0.62rem", opacity: 0.7 }}>Ideas Today</div>
            <div style={{ fontWeight: 800, fontSize: "1.2rem" }}>
              {ideaCount}
              <span style={{ fontSize: "0.7rem", opacity: 0.6 }}> / {s.suggested_max_ideas ?? "—"} max</span>
            </div>
          </div>
        )}
      </div>

      {/* Exposure vs cash meter */}
      <div style={{ marginTop: 14, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <div className="flex justify-between" style={{ fontSize: "0.7rem", marginBottom: 4 }}>
            <span style={{ opacity: 0.75 }}>Suggested Exposure</span>
            <span style={{ fontWeight: 700, color }}>{s.exposure_pct}%</span>
          </div>
          <Meter pct={s.exposure_pct} color={color} />
        </div>
        <div>
          <div className="flex justify-between" style={{ fontSize: "0.7rem", marginBottom: 4 }}>
            <span style={{ opacity: 0.75 }}>Cash Allocation</span>
            <span style={{ fontWeight: 700 }}>{s.cash_pct}%</span>
          </div>
          <Meter pct={s.cash_pct} color="#94a3b8" />
        </div>
      </div>

      {/* Leading / lagging sectors */}
      <div style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 18 }}>
        {s.leading_sectors && s.leading_sectors.length > 0 && (
          <div>
            <div style={{ fontSize: "0.64rem", opacity: 0.7, display: "flex", alignItems: "center", gap: 4 }}>
              <TrendingUp size={12} color="#22c55e" /> Leading
            </div>
            <div style={{ fontWeight: 600, fontSize: "0.82rem" }}>{s.leading_sectors.slice(0, 4).join(" · ")}</div>
          </div>
        )}
        {s.lagging_sectors && s.lagging_sectors.length > 0 && (
          <div>
            <div style={{ fontSize: "0.64rem", opacity: 0.7, display: "flex", alignItems: "center", gap: 4 }}>
              <TrendingDown size={12} color="#ef4444" /> Lagging
            </div>
            <div style={{ fontWeight: 600, fontSize: "0.82rem", opacity: 0.85 }}>
              {s.lagging_sectors.slice(0, 4).join(" · ")}
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: 12, fontSize: "0.78rem", opacity: 0.82 }}>{s.advisory}</div>
    </div>
  );
}

export default ExposureRegimePanel;
