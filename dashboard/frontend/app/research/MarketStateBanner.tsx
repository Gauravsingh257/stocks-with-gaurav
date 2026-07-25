"use client";

/**
 * MarketStateBanner — PR1 (Regime Governor).
 *
 * Surfaces the market-regime "exposure state" at the top of the Research page:
 * suggested exposure vs cash %, the graduated policy, and — when the market is
 * Defensive / Risk-Off with no qualifying ideas — an honest "Hold Cash" message
 * instead of an empty placeholder or manufactured picks.
 *
 * Informational even when the governor is disabled (it reads /api/market/state,
 * which never changes what the feed serves). Fails silently on any error.
 */

import { useEffect, useState } from "react";
import { Shield, TrendingUp } from "lucide-react";
import { api, type MarketStateResponse } from "@/lib/api";

const TONE: Record<string, { bg: string; border: string; fg: string }> = {
  "🟢 Aggressive": { bg: "rgba(34,197,94,0.10)", border: "rgba(34,197,94,0.35)", fg: "#22c55e" },
  "🟢 Normal": { bg: "rgba(34,197,94,0.08)", border: "rgba(34,197,94,0.30)", fg: "#22c55e" },
  "🟡 Defensive": { bg: "rgba(234,179,8,0.10)", border: "rgba(234,179,8,0.40)", fg: "#eab308" },
  "🔴 Risk-Off": { bg: "rgba(239,68,68,0.12)", border: "rgba(239,68,68,0.45)", fg: "#ef4444" },
};

const STATE_LABEL: Record<string, string> = {
  STRONG_BULL: "Strong Bull",
  WEAK_BULL: "Weak Bull",
  SIDEWAYS: "Sideways",
  CORRECTION: "Correction",
  BEAR: "Bear",
  UNKNOWN: "Unknown",
};

export function MarketStateBanner({ finalCount }: { finalCount: number }) {
  const [state, setState] = useState<MarketStateResponse | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .marketState()
      .then((s) => alive && setState(s))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!state) return null;

  const tone = TONE[state.exposure_label] ?? TONE["🟢 Normal"];
  const defensive = state.exposure_pct < 60;
  const cashMode = defensive && finalCount === 0;

  return (
    <div
      className="glass"
      style={{
        padding: 16,
        marginBottom: 16,
        borderRadius: 12,
        background: tone.bg,
        border: `1px solid ${tone.border}`,
      }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {defensive ? <Shield size={18} color={tone.fg} /> : <TrendingUp size={18} color={tone.fg} />}
          <div>
            <div style={{ fontSize: "0.72rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: 0.5 }}>
              Market Regime
            </div>
            <div style={{ fontWeight: 700, fontSize: "0.98rem", color: tone.fg }}>
              {state.exposure_label} · {STATE_LABEL[state.market_state] ?? state.market_state}
            </div>
            {typeof state.market_health === "number" && (
              <div style={{ fontSize: "0.66rem", opacity: 0.8, marginTop: 2 }}>
                Market Health <strong>{state.market_health}/100</strong>
                {state.opportunity_level && <> · Opportunity: <strong>{state.opportunity_level}</strong></>}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 22, alignItems: "center" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: "0.7rem", opacity: 0.7 }}>Suggested Exposure</div>
            <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>{state.exposure_pct}%</div>
          </div>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: "0.7rem", opacity: 0.7 }}>Cash</div>
            <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>{state.cash_pct}%</div>
          </div>
          {state.leading_sectors && state.leading_sectors.length > 0 && (
            <div style={{ maxWidth: 260 }}>
              <div style={{ fontSize: "0.7rem", opacity: 0.7 }}>Leading Sectors</div>
              <div style={{ fontWeight: 600, fontSize: "0.82rem" }}>
                {state.leading_sectors.slice(0, 3).join(" · ")}
              </div>
            </div>
          )}
        </div>
      </div>

      {cashMode && (
        <div
          style={{
            marginTop: 14,
            paddingTop: 14,
            borderTop: `1px dashed ${tone.border}`,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div style={{ fontWeight: 700, fontSize: "0.95rem", color: tone.fg }}>
            No High-Quality Buying Opportunities Today
          </div>
          <div style={{ fontSize: "0.82rem", opacity: 0.85 }}>
            Suggested: Hold Cash ({state.cash_pct}%). {state.advisory} Waiting is a valid decision — cash is a position.
          </div>
        </div>
      )}

      {!cashMode && defensive && (
        <div style={{ marginTop: 10, fontSize: "0.8rem", opacity: 0.8 }}>{state.advisory}</div>
      )}
    </div>
  );
}

export default MarketStateBanner;
