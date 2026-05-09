"use client";

import { Brain, Compass, ShieldAlert, Zap } from "lucide-react";
import type { OIInterpretation, OIGuidanceUnderlying } from "./types";

export function OIInterpretationPanel({ data }: { data: OIInterpretation | null | undefined }) {
  if (!data) return null;

  const lines = data.summary_lines ?? [];
  const events = data.events ?? [];
  const narratives = data.strike_narratives ?? [];
  const guidance = data.guidance ?? {};
  const delta = data.delta_vs_prior;

  const ulKeys = Object.keys(guidance);

  return (
    <div
      className="glass"
      style={{
        padding: 18,
        marginBottom: 16,
        border: "1px solid rgba(0,212,255,0.2)",
        background:
          "linear-gradient(135deg, rgba(0,212,255,0.06) 0%, rgba(8,14,28,0.65) 45%, rgba(5,10,22,0.75) 100%)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background: "rgba(0,212,255,0.12)",
            border: "1px solid rgba(0,212,255,0.25)",
            display: "grid",
            placeItems: "center",
          }}
        >
          <Brain size={18} color="var(--accent)" aria-hidden />
        </div>
        <div>
          <div style={{ fontSize: "0.62rem", fontWeight: 800, letterSpacing: 0.14, color: "var(--text-dim)", textTransform: "uppercase" }}>
            Interpretation engine
          </div>
          <div style={{ fontSize: "1rem", fontWeight: 850, color: "var(--text-primary)" }}>Actionable OI read</div>
        </div>
        {delta?.has_prior === false && (
          <span style={{ marginLeft: "auto", fontSize: "0.62rem", color: "var(--warning)", fontWeight: 700 }}>
            Building baseline — delta unlocks on next refresh
          </span>
        )}
      </div>

      {lines.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, letterSpacing: 0.1, color: "var(--text-dim)", marginBottom: 8 }}>
            SUMMARY
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", fontSize: "0.82rem", lineHeight: 1.55 }}>
            {lines.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {events.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, letterSpacing: 0.1, color: "var(--text-dim)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <Zap size={12} /> REGIME / FLOW SIGNALS
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {events.map((ev, i) => (
              <div
                key={i}
                style={{
                  padding: "10px 12px",
                  borderRadius: 8,
                  border: "1px solid rgba(255,255,255,0.08)",
                  background: "rgba(255,255,255,0.03)",
                  fontSize: "0.78rem",
                  color: "var(--text-secondary)",
                }}
              >
                <span style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--accent)", marginRight: 8 }}>
                  {(ev.type || "note").replace(/_/g, " ").toUpperCase()}
                </span>
                {ev.text}
              </div>
            ))}
          </div>
        </div>
      )}

      {ulKeys.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, letterSpacing: 0.1, color: "var(--text-dim)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <Compass size={12} /> DECISION GUIDANCE (INDEX CONTEXT)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 }}>
            {ulKeys.map((k) => {
              const g: OIGuidanceUnderlying = guidance[k];
              const zone = g.avoid_trade_zone;
              const bull = g.bullish_above != null ? String(g.bullish_above) : "—";
              const bear = g.bearish_below != null ? String(g.bearish_below) : "—";
              return (
                <div key={k} style={{ padding: 12, borderRadius: 10, border: "1px solid rgba(255,255,255,0.08)", background: "rgba(0,0,0,0.2)" }}>
                  <div style={{ fontWeight: 850, fontSize: "0.85rem", marginBottom: 8 }}>{k}</div>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>
                    <div>
                      <strong style={{ color: "var(--success)" }}>Bullish bias</strong> ideally above{" "}
                      <span style={{ fontFamily: "monospace" }}>{bull}</span>
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <strong style={{ color: "var(--danger)" }}>Bearish bias</strong> ideally below{" "}
                      <span style={{ fontFamily: "monospace" }}>{bear}</span>
                    </div>
                    {zone && zone.length >= 2 && (
                      <div style={{ marginTop: 6, color: "var(--warning)" }}>
                        <ShieldAlert size={12} style={{ display: "inline", verticalAlign: "middle", marginRight: 4 }} />
                        Chop zone (ATM band): {zone[0]}–{zone[1]}
                      </div>
                    )}
                    {g.high_volatility_zone ? (
                      <div style={{ marginTop: 6, fontSize: "0.68rem", color: "var(--warning)" }}>Elevated conviction / volatility context</div>
                    ) : null}
                    {typeof g.structure_note === "string" && g.structure_note ? (
                      <div style={{ marginTop: 8, fontSize: "0.68rem", color: "var(--text-dim)" }}>{g.structure_note}</div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {narratives.length > 0 && (
        <div>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, letterSpacing: 0.1, color: "var(--text-dim)", marginBottom: 8 }}>
            STRIKE NARRATIVES (DELTA VS PRIOR)
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {narratives.slice(0, 12).map((n, i) => (
              <div
                key={`${n.underlying}-${n.strike}-${i}`}
                style={{
                  flex: "1 1 280px",
                  padding: "8px 10px",
                  borderRadius: 8,
                  border:
                    n.tone === "constructive"
                      ? "1px solid rgba(0,224,150,0.25)"
                      : n.tone === "bullish_relief"
                        ? "1px solid rgba(0,212,255,0.22)"
                        : "1px solid rgba(255,165,2,0.22)",
                  background: "rgba(255,255,255,0.02)",
                  fontSize: "0.74rem",
                }}
              >
                <strong style={{ color: "var(--text-primary)" }}>
                  {n.underlying} {n.strike}
                </strong>{" "}
                — {n.headline}. {n.detail}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
