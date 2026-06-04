"use client";

import { useEffect, useState } from "react";
import { Activity, CheckCircle2, Circle, FlaskConical } from "lucide-react";
import { api, type AnchorShadowStatus } from "@/lib/api";

const OVERALL_STYLE: Record<string, { label: string; color: string; bg: string; border: string }> = {
  READY:      { label: "READY",       color: "#00e096", bg: "rgba(0,224,150,0.12)",  border: "rgba(0,224,150,0.4)" },
  COLLECTING: { label: "COLLECTING",  color: "#5b9cf6", bg: "rgba(91,156,246,0.12)", border: "rgba(91,156,246,0.4)" },
  NOT_READY:  { label: "NOT READY",   color: "#ff4d6d", bg: "rgba(255,77,109,0.12)", border: "rgba(255,77,109,0.4)" },
  UNKNOWN:    { label: "UNKNOWN",     color: "#94a3b8", bg: "rgba(148,163,184,0.1)", border: "rgba(148,163,184,0.3)" },
};

const C_LABEL: Record<string, string> = {
  C1_count_stable: "C1 · count stable",
  C2_actionable: "C2 · actionable ≥60%",
  C3_avg_distance: "C3 · avg dist ≤6%",
  C4_median_rr: "C4 · median RR ≥2.0",
  C5_stable_window: "C5 · 3+ stable sessions",
};

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ padding: "8px 10px", borderRadius: 8, background: "rgba(2,6,23,0.35)", border: "1px solid rgba(148,163,184,0.16)" }}>
      <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: "1.02rem", fontWeight: 850, color: accent ?? "var(--text-primary)" }}>{value}</div>
    </div>
  );
}

export default function AnchorShadowChip() {
  const [data, setData] = useState<AnchorShadowStatus | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    api.anchorShadowStatus()
      .then((res) => { if (alive) setData(res ?? null); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, []);

  const overall = data?.overall ?? "UNKNOWN";
  const ov = OVERALL_STYLE[overall] ?? OVERALL_STYLE.UNKNOWN;
  const latest = data?.latest ?? null;

  return (
    <section className="glass" style={{ padding: 16, display: "grid", gap: 12, border: "1px solid rgba(91,156,246,0.18)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FlaskConical size={17} color="#5b9cf6" aria-hidden />
          <div>
            <h3 className="m-0" style={{ fontSize: "0.92rem", fontWeight: 850, color: "var(--text-primary)" }}>
              Anchor10 Shadow Validation
            </h3>
            <p style={{ margin: "2px 0 0", fontSize: "0.68rem", color: "var(--text-dim)" }}>
              Observational soak of ENTRY_ANCHOR_MAX_GAP_PCT=10 · source of truth: <code>/api/research/anchor-shadow-status</code>
            </p>
          </div>
        </div>
        <span style={{ fontSize: "0.74rem", fontWeight: 900, padding: "5px 12px", borderRadius: 999, color: ov.color, background: ov.bg, border: `1px solid ${ov.border}` }}>
          {ov.label}
        </span>
      </div>

      {!loaded ? (
        <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>Loading validation status…</div>
      ) : data?.error || !data ? (
        <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
          Status unavailable right now. {data?.error ? <code>{data.error}</code> : null}
        </div>
      ) : (
        <>
          {/* Sessions + C1–C5 */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: "0.72rem", fontWeight: 800, color: "var(--text-secondary)" }}>
              Sessions collected: <span style={{ color: "var(--text-primary)" }}>{data.session_count}/{data.sessions_required}</span>
            </span>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {Object.entries(data.criteria).map(([key, c]) => (
              <span
                key={key}
                title={c.rule}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  fontSize: "0.68rem", fontWeight: 750, padding: "4px 9px", borderRadius: 7,
                  color: c.pass ? "#00e096" : "var(--text-dim)",
                  background: c.pass ? "rgba(0,224,150,0.1)" : "rgba(148,163,184,0.08)",
                  border: `1px solid ${c.pass ? "rgba(0,224,150,0.3)" : "rgba(148,163,184,0.2)"}`,
                }}
              >
                {c.pass ? <CheckCircle2 size={12} /> : <Circle size={12} />}
                {C_LABEL[key] ?? key}
              </span>
            ))}
          </div>

          {/* Daily numbers from the latest recorded session */}
          {latest ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
                <Stat label="Current signals" value={String(latest.current_count)} />
                <Stat label="Anchor10 signals" value={String(latest.anchor_count)} accent="#5b9cf6" />
                <Stat label="Daily actionable" value={`${latest.actionable_pct}%`} accent={latest.actionable_pct >= 60 ? "#00e096" : "#ff4d6d"} />
                <Stat label="Daily avg distance" value={`${latest.avg_distance_pct}%`} accent={latest.avg_distance_pct <= 6 ? "#00e096" : "#ff4d6d"} />
              </div>
              <div style={{ fontSize: "0.66rem", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <Activity size={11} />
                Latest session {latest.date} · median rem. RR {latest.median_remaining_rr ?? "—"} · count Δ {latest.count_drop_pct === 0 ? "0" : `-${latest.count_drop_pct}`}%
                {latest.breaches.length > 0 && (
                  <span style={{ color: "#ff4d6d", fontWeight: 800 }}>· breach: {latest.breaches.join("; ")}</span>
                )}
              </div>
            </>
          ) : (
            <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)" }}>
              No sessions recorded yet — the recorder runs at 09:20 IST on trading days.
            </div>
          )}

          <p style={{ margin: 0, fontSize: "0.64rem", color: "var(--text-dim)", lineHeight: 1.5 }}>
            {data.recommendation} ENTRY_ANCHOR_MAX_GAP_PCT stays <strong>30</strong> and STRUCTURAL_TARGET_CAP stays <strong>0</strong> until all of C1–C5 pass.
          </p>
        </>
      )}
    </section>
  );
}
