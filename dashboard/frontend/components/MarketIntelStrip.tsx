"use client";

import { useEffect, useState } from "react";
import { Gauge, TrendingUp } from "lucide-react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useMergedIndexLtp } from "@/lib/realtimeRegistry";
import { api, type MISnapshot } from "@/lib/api";

function fmtNum(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n >= 1 ? Math.round(n).toLocaleString("en-IN") : n.toFixed(2);
}

export default function MarketIntelStrip() {
  const { snapshot } = useEngineSocket();
  const indexLtpMerged = useMergedIndexLtp(snapshot?.index_ltp);
  const [mi, setMi] = useState<MISnapshot | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      api
        .marketIntelSnapshot()
        .then((d) => {
          if (!cancelled) setMi(d);
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 120_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const nifty = indexLtpMerged["NIFTY 50"];
  const bank = indexLtpMerged["NIFTY BANK"];
  const regime = snapshot?.market_regime ?? "NEUTRAL";

  const niftyBias =
    snapshot?.setup_d_state?.["NIFTY"]?.bias ??
    snapshot?.setup_d_state?.["NIFTY 50"]?.bias ??
    regime;

  const intelHint = mi?.fetched_at
    ? `Intel refreshed ${new Date(mi.fetched_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`
    : "Intel · syncing";

  const volLabel = mi?.macro ? "Macro context loaded" : "Volatility · monitoring";

  const liquidity = mi?.mf_flows ? "Flows · live context" : "Liquidity · monitoring";

  return (
    <div
      style={{
        borderRadius: 12,
        border: "1px solid rgba(255,255,255,0.08)",
        background: "rgba(255,255,255,0.03)",
        padding: "10px 12px",
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
        gap: 10,
      }}
    >
      <IntelCell icon={<TrendingUp size={14} />} label="Tape regime" value={regime} hint="Live classification" />
      <IntelCell icon={<TrendingUp size={14} />} label="Structure bias" value={String(niftyBias)} hint="Engine snapshot" />
      <IntelCell icon={<TrendingUp size={14} />} label="NIFTY 50" value={fmtNum(nifty)} hint="Reference LTP" />
      <IntelCell icon={<TrendingUp size={14} />} label="NIFTY BANK" value={fmtNum(bank)} hint="Reference LTP" />
      <IntelCell icon={<Gauge size={14} />} label="Volatility" value={volLabel} hint={intelHint} />
      <IntelCell icon={<Gauge size={14} />} label="Liquidity" value={String(liquidity)} hint={intelHint} />
    </div>
  );
}

function IntelCell({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
      <span style={{ color: "var(--accent)", marginTop: 2, flexShrink: 0 }}>{icon}</span>
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: "0.52rem",
            fontWeight: 700,
            letterSpacing: 0.08,
            color: "var(--text-dim)",
            textTransform: "uppercase",
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: "0.78rem",
            fontWeight: 800,
            color: "var(--text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          title={value}
        >
          {value}
        </div>
        <div style={{ fontSize: "0.58rem", color: "var(--text-dim)", marginTop: 2 }}>{hint}</div>
      </div>
    </div>
  );
}
