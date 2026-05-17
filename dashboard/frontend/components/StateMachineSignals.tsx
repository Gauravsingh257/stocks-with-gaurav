"use client";

/**
 * G2-6 Rung B — dedicated, clearly-labelled surface for the NEW
 * planned-execution (state-machine) engine. Reads the ISOLATED
 * /api/research/state-machine-signals endpoint (never the legacy feed),
 * so what shows here is unambiguously the new engine. Self-contained:
 * own fetch + own error handling — it can never break the rest of the
 * page. Validation phase: planned LIMIT setups, NEVER auto-traded.
 */

import { useCallback, useEffect, useState } from "react";
import { FlaskConical, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { isMarketLive, offHoursNotice } from "@/lib/marketSession";

type Sig = Record<string, unknown>;

const num = (v: unknown): number | null => {
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? n : null;
};

function Badge() {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "3px 9px",
        borderRadius: 999,
        fontSize: "0.62rem",
        fontWeight: 700,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        color: "#a78bfa",
        background: "rgba(139,92,246,0.14)",
        border: "1px solid rgba(139,92,246,0.4)",
        whiteSpace: "nowrap",
      }}
    >
      <FlaskConical size={11} /> Planned-Execution · Validation
    </span>
  );
}

export default function StateMachineSignals() {
  const [signals, setSignals] = useState<Sig[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.stateMachineSignals(50);
      setSignals(Array.isArray(res?.signals) ? res.signals : []);
      setErrored(false);
    } catch {
      setErrored(true);
      setSignals([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 120_000);
    return () => clearInterval(id);
  }, [load]);

  const count = signals?.length ?? 0;

  return (
    <div
      className="glass"
      style={{
        padding: 16,
        borderRadius: 14,
        border: "1px solid rgba(139,92,246,0.28)",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 32, height: 32, borderRadius: 8, background: "rgba(139,92,246,0.12)", border: "1px solid rgba(139,92,246,0.3)", display: "grid", placeItems: "center" }}>
            <FlaskConical size={16} color="#a78bfa" />
          </div>
          <div>
            <h2 style={{ margin: 0, fontSize: "1.02rem", fontWeight: 700 }}>
              Planned-Execution Signals
            </h2>
            <p style={{ margin: "2px 0 0", fontSize: "0.72rem", color: "var(--text-secondary)" }}>
              New engine · Validation phase — backtest-proven 5/5 windows · planned LIMIT entries · <strong style={{ color: "#a78bfa" }}>not auto-traded</strong>
            </p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Badge />
          <button
            onClick={load}
            title="Refresh"
            style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "5px 10px", borderRadius: 8, fontSize: "0.7rem", color: "var(--text-secondary)", background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)", cursor: "pointer" }}
          >
            <RefreshCw size={11} /> Refresh
          </button>
        </div>
      </div>

      {loading && count === 0 ? (
        <div style={{ padding: 10, color: "var(--text-secondary)", fontSize: "0.78rem" }}>
          Loading planned-execution signals…
        </div>
      ) : count === 0 ? (
        <div style={{ padding: 12, color: "var(--text-secondary)", fontSize: "0.78rem", background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)", borderRadius: 10 }}>
          {errored && isMarketLive()
            ? "Signals momentarily unavailable — retrying."
            : (offHoursNotice() ? offHoursNotice() + " " : "")}
          No actionable planned setups currently. This engine fires only on a
          genuine high-quality setup — <strong>zero is a valid, honest
          output</strong>, not an error.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {signals!.map((s, i) => {
            const entry = num(s.entry_price);
            const sl = num(s.stop_loss);
            const tgts = Array.isArray(s.targets) ? (s.targets as unknown[]) : [];
            const target = num(tgts[tgts.length - 1]) ?? num(s.target_1);
            const rr =
              entry != null && sl != null && target != null && Math.abs(entry - sl) > 0
                ? Math.abs(target - entry) / Math.abs(entry - sl)
                : null;
            return (
              <div
                key={(s.id as number) ?? (s.symbol as string) ?? i}
                style={{
                  padding: 12,
                  borderRadius: 10,
                  background: "rgba(139,92,246,0.05)",
                  border: "1px solid rgba(139,92,246,0.22)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 700, fontSize: "0.9rem" }}>
                    {String(s.symbol ?? "—")}
                  </span>
                  <Badge />
                </div>
                <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: "0.74rem", color: "var(--text-secondary)" }}>
                  <span>Planned entry (LIMIT) <strong style={{ color: "var(--text)" }}>{entry ?? "—"}</strong></span>
                  <span>SL <strong style={{ color: "#ff6b81" }}>{sl ?? "—"}</strong></span>
                  <span>Target <strong style={{ color: "#00d18c" }}>{target ?? "—"}</strong></span>
                  <span>R:R <strong style={{ color: "var(--text)" }}>{rr != null ? `1:${rr.toFixed(1)}` : "—"}</strong></span>
                </div>
                <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", lineHeight: 1.45 }}>
                  Wait for the zone — no position is opened automatically.{" "}
                  {typeof s.reasoning === "string" ? (s.reasoning as string).slice(0, 160) : ""}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
