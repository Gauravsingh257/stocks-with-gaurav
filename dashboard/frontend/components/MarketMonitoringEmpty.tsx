"use client";

import { useMemo } from "react";
import { Activity, Radar } from "lucide-react";
import { useEngineSocket } from "@/lib/useWebSocket";

export default function MarketMonitoringEmpty({
  title = "Market monitoring active",
  subtitle,
}: {
  title?: string;
  subtitle?: string;
}) {
  const { snapshot } = useEngineSocket();

  const hints = useMemo(() => {
    const regime = snapshot?.market_regime ?? "NEUTRAL";
    const zones = snapshot?.zone_state ? Object.keys(snapshot.zone_state).length : 0;
    const sig = snapshot?.signals_today ?? 0;
    return [
      `Regime: ${regime}`,
      zones ? `${zones} symbols under zone tracking` : "Scanning liquidity pockets",
      `Automation signals today: ${sig}`,
    ];
  }, [snapshot]);

  return (
    <div
      style={{
        padding: "22px 18px",
        borderRadius: 14,
        border: "1px solid rgba(0,212,255,0.14)",
        background:
          "linear-gradient(135deg, rgba(0,212,255,0.06) 0%, rgba(5,12,28,0.55) 55%, rgba(8,18,38,0.7) 100%)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 12,
            background: "rgba(0,212,255,0.12)",
            border: "1px solid rgba(0,212,255,0.22)",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
          }}
        >
          <Radar size={20} color="var(--accent)" aria-hidden />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: "0.95rem",
              fontWeight: 850,
              color: "var(--text-primary)",
              marginBottom: 6,
            }}
          >
            {title}
          </div>
          <p style={{ margin: 0, fontSize: "0.8rem", color: "var(--text-secondary)", lineHeight: 1.55 }}>
            {subtitle ??
              "High-conviction setups only surface when structure, liquidity, and execution layers align. The desk stays live — watch Discovery and Watchlist for names approaching entry."}
          </p>
          <ul style={{ margin: "12px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 6 }}>
            {hints.map((h) => (
              <li
                key={h}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: "0.74rem",
                  color: "var(--text-dim)",
                }}
              >
                <Activity size={13} color="#00e096" aria-hidden />
                {h}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
