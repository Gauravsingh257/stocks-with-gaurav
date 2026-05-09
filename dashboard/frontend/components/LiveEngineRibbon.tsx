"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Cpu,
  Radio,
  RefreshCw,
  Send,
  ShieldCheck,
  Timer,
  Zap,
} from "lucide-react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useHealth } from "@/lib/useHealth";
import { api } from "@/lib/api";

function fmtAgeSec(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  if (sec < 5) return "just now";
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  return `${Math.round(sec / 3600)}h ago`;
}

export default function LiveEngineRibbon() {
  const { snapshot, status: wsStatus } = useEngineSocket();
  const health = useHealth();
  const [telegramHint, setTelegramHint] = useState<string>("…");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      api
        .signals({ limit: 5 })
        .then((page) => {
          if (cancelled) return;
          const first = page.signals[0];
          if (!first?.timestamp) {
            setTelegramHint("Alert queue quiet · monitoring");
            return;
          }
          const ts = new Date(String(first.timestamp).replace(" ", "T")).getTime();
          if (Number.isNaN(ts)) {
            setTelegramHint(`${page.count} delivery log rows`);
            return;
          }
          const ageMin = Math.max(0, Math.round((Date.now() - ts) / 60_000));
          setTelegramHint(
            ageMin < 2
              ? `Last alert ${ageMin === 0 ? "<1" : ageMin}m ago`
              : `Last alert ${ageMin}m ago`,
          );
        })
        .catch(() => {
          if (!cancelled) setTelegramHint("Delivery status syncing");
        });
    };
    load();
    const id = setInterval(load, 45_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const engineLabel = useMemo(() => {
    const h = health?.engine_status;
    if (h === "running") return { text: "ENGINE LIVE", tone: "#00e096" };
    if (h === "stale") return { text: "ENGINE SYNC", tone: "#ffa502" };
    return { text: "ENGINE STANDBY", tone: "#8899bb" };
  }, [health?.engine_status]);

  const kiteOk = health?.kite_connected !== false && health?.token_present !== false;
  const kiteLabel = kiteOk ? "Kite OK" : "Kite reconnect";

  const cycleAge =
    snapshot?.engine_last_cycle_age_sec ?? health?.engine_last_cycle_age_sec ?? null;

  const snapAgeSec = useMemo(() => {
    const t = snapshot?.snapshot_time;
    if (!t) return null;
    const ms = new Date(t).getTime();
    if (Number.isNaN(ms)) return null;
    return (Date.now() - ms) / 1000;
  }, [snapshot?.snapshot_time, tick]);

  const symbolsMonitored = useMemo(() => {
    const zs = snapshot?.zone_state;
    if (zs && typeof zs === "object") return Object.keys(zs).length;
    const td = snapshot?.traded_today;
    if (Array.isArray(td)) return td.length;
    return null;
  }, [snapshot?.zone_state, snapshot?.traded_today]);

  const wsHint =
    wsStatus === "connected" ? "WS" : wsStatus === "polling" ? "REST" : "···";

  const uptime = health?.engine_uptime_human ?? "—";
  const scansToday = snapshot?.signals_today ?? 0;

  return (
    <div
      style={{
        marginLeft: "-1rem",
        marginRight: "-1rem",
        padding: "10px 16px",
        background:
          "linear-gradient(105deg, rgba(6,11,24,0.97) 0%, rgba(12,24,48,0.94) 45%, rgba(8,18,38,0.96) 100%)",
        borderBottom: "1px solid rgba(0,212,255,0.18)",
        borderTop: "1px solid rgba(0,224,150,0.08)",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.04), 0 12px 40px rgba(0,0,0,0.35)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
          maxWidth: 1800,
          margin: "0 auto",
          rowGap: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: 999,
              background: "rgba(0,212,255,0.1)",
              border: "1px solid rgba(0,212,255,0.28)",
              fontSize: "0.62rem",
              fontWeight: 900,
              letterSpacing: 0.12,
              color: "#c4f1ff",
            }}
          >
            <Cpu size={13} color="#00d4ff" aria-hidden />
            LIVE TERMINAL
          </span>
          <span
            style={{
              fontSize: "0.58rem",
              fontWeight: 800,
              letterSpacing: 0.14,
              color: engineLabel.tone,
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: engineLabel.tone,
                boxShadow: `0 0 10px ${engineLabel.tone}`,
                animation: "pulse 2.2s ease-in-out infinite",
              }}
            />
            {engineLabel.text}
          </span>
        </div>

        <RibbonDivider />

        <RibbonChip icon={<Radio size={12} />} label="Transport" value={wsHint} dim={wsStatus !== "connected"} />
        <RibbonChip icon={<ShieldCheck size={12} />} label="Broker" value={kiteLabel} accent={!kiteOk} />
        <RibbonChip icon={<Timer size={12} />} label="Last cycle" value={fmtAgeSec(typeof cycleAge === "number" ? cycleAge : null)} />
        <RibbonChip icon={<RefreshCw size={12} />} label="Data age" value={fmtAgeSec(snapAgeSec)} />
        <RibbonChip icon={<Send size={12} />} label="Telegram" value={telegramHint} />
        <RibbonChip icon={<Zap size={12} />} label="Signals today" value={`${scansToday}`} />
        <RibbonChip
          icon={<Activity size={12} />}
          label="Zones tracked"
          value={symbolsMonitored != null ? String(symbolsMonitored) : "—"}
        />
        <RibbonChip icon={<Timer size={12} />} label="Engine uptime" value={uptime} />

        <div style={{ flex: 1, minWidth: 8 }} />

        <div
          style={{
            fontSize: "0.58rem",
            color: "var(--text-dim)",
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
          }}
        >
          {snapshot?.market_regime && (
            <span style={{ color: "var(--text-secondary)", marginRight: 10 }}>
              Regime <strong style={{ color: "var(--text-primary)" }}>{snapshot.market_regime}</strong>
            </span>
          )}
          {snapshot?.snapshot_time && (
            <span title="Snapshot timestamp">
              Sync{" "}
              {new Date(snapshot.snapshot_time).toLocaleTimeString("en-IN", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </span>
          )}
        </div>
      </div>
      <style jsx global>{`
        @keyframes pulse {
          0%,
          100% {
            opacity: 1;
          }
          50% {
            opacity: 0.45;
          }
        }
      `}</style>
    </div>
  );
}

function RibbonDivider() {
  return <div style={{ width: 1, height: 22, background: "rgba(255,255,255,0.08)", flexShrink: 0 }} />;
}

function RibbonChip({
  icon,
  label,
  value,
  dim,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  dim?: boolean;
  accent?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "5px 10px",
        borderRadius: 8,
        background: "rgba(255,255,255,0.035)",
        border: `1px solid ${accent ? "rgba(255,71,87,0.35)" : "rgba(255,255,255,0.07)"}`,
        opacity: dim ? 0.75 : 1,
        flexShrink: 0,
      }}
    >
      <span style={{ color: "var(--accent)", display: "grid", placeItems: "center", opacity: 0.9 }}>{icon}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 0, lineHeight: 1.15 }}>
        <span
          style={{
            fontSize: "0.52rem",
            fontWeight: 700,
            letterSpacing: 0.06,
            color: "var(--text-dim)",
            textTransform: "uppercase",
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontSize: "0.68rem",
            fontWeight: 750,
            color: "var(--text-primary)",
            whiteSpace: "nowrap",
            maxWidth: 160,
          }}
          title={value}
        >
          {value}
        </span>
      </div>
    </div>
  );
}
