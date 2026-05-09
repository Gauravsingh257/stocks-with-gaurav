"use client";

/**
 * Intelligence-first OI surface — narrative, deltas, guidance, strike cards.
 * Data comes only from backend interpretation (Redis-enriched snapshot / WS).
 */
import {
  Brain,
  Gauge,
  Shield,
  Sparkles,
  TrendingUp,
  Zap,
  Activity,
  History,
  Radio,
  Crosshair,
  Target,
} from "lucide-react";
import type { OIInterpretation } from "./types";

function pctBar(label: string, value: number | undefined, colorVar: string) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.68rem", marginBottom: 4 }}>
        <span style={{ color: "var(--text-dim)", fontWeight: 700 }}>{label}</span>
        <span style={{ fontFamily: "monospace", color: colorVar }}>{v}%</span>
      </div>
      <div style={{ height: 8, borderRadius: 6, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
        <div
          style={{
            width: `${v}%`,
            height: "100%",
            borderRadius: 6,
            background: `linear-gradient(90deg, ${colorVar}, transparent)`,
            opacity: 0.95,
          }}
        />
      </div>
    </div>
  );
}

function bulletToneColor(tone?: string) {
  if (tone === "positive") return "var(--success)";
  if (tone === "caution") return "var(--warning)";
  return "var(--text-secondary)";
}

export function MarketIntelligenceSuite({ data }: { data: OIInterpretation | null | undefined }) {
  if (!data) return null;

  const regime = data.market_regime;
  const story = data.market_story;
  const wc = data.what_changed;
  const inst = data.institutional_positioning;
  const conf = data.confidence;
  const guidanceKeys = Object.keys(data.guidance ?? {});
  const strikes = data.strike_intelligence ?? [];
  const shifts = data.oi_shifts ?? [];
  const flow = data.flow_strength;
  const evo = data.strike_evolution ?? [];
  const intent2 = data.institutional_intent_v2;
  const smart = data.smart_trading_actions;
  const sess = data.session_evolution;
  const p15 = data.phase15_meta;
  const timeline = sess?.story_timeline ?? [];
  const cross = sess?.cross_session;

  const arrowSymbol = (a?: string) => {
    if (a === "up") return "↑";
    if (a === "down") return "↓";
    if (a === "trap") return "⚠";
    return "↔";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Hero */}
      <div
        className="glass"
        style={{
          padding: 20,
          border: "1px solid rgba(0,212,255,0.22)",
          background:
            "linear-gradient(145deg, rgba(0,212,255,0.09) 0%, rgba(8,14,28,0.72) 42%, rgba(5,10,22,0.85) 100%)",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: 14, marginBottom: 16 }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: "rgba(0,212,255,0.12)",
              border: "1px solid rgba(0,212,255,0.28)",
              display: "grid",
              placeItems: "center",
            }}
          >
            <Brain size={22} color="var(--accent)" aria-hidden />
          </div>
          <div style={{ flex: "1 1 220px" }}>
            <div
              style={{
                fontSize: "0.58rem",
                fontWeight: 800,
                letterSpacing: 0.14,
                color: "var(--text-dim)",
                textTransform: "uppercase",
                marginBottom: 6,
              }}
            >
              Market intelligence engine · {data.engine_version ?? "oi_interp"}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, marginBottom: 8 }}>
              {regime?.label && (
                <span
                  style={{
                    padding: "4px 12px",
                    borderRadius: 999,
                    fontSize: "0.72rem",
                    fontWeight: 850,
                    border: "1px solid rgba(0,224,150,0.35)",
                    background: "rgba(0,224,150,0.08)",
                    color: "var(--success)",
                  }}
                >
                  {regime.label}
                </span>
              )}
              {typeof regime?.confidence === "number" && (
                <span style={{ fontSize: "0.68rem", color: "var(--text-dim)" }}>
                  Regime confidence ~{regime.confidence}%
                </span>
              )}
              {data.generated_at && (
                <span style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>
                  Generated {new Date(data.generated_at).toLocaleTimeString()}
                </span>
              )}
              {typeof p15?.generation_latency_ms === "number" && (
                <span style={{ fontSize: "0.58rem", color: "var(--text-dim)" }}>{p15.generation_latency_ms}ms</span>
              )}
            </div>
            <h2 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 900, color: "var(--text-primary)", lineHeight: 1.35 }}>
              {story?.headline ?? "Market positioning snapshot"}
            </h2>
            {(story?.paragraphs?.length ?? 0) > 0 && (
              <div style={{ marginTop: 12, fontSize: "0.84rem", color: "var(--text-secondary)", lineHeight: 1.65 }}>
                {story!.paragraphs!.map((p, i) => (
                  <p key={i} style={{ margin: i === 0 ? "0 0 8px" : "0 0 8px" }}>
                    {p}
                  </p>
                ))}
              </div>
            )}
          </div>

          <div style={{ flex: "1 1 240px", minWidth: 200 }}>
            <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 8, letterSpacing: 0.08 }}>
              CONVICTION ( HEURISTIC )
            </div>
            {pctBar("Bullish tilt", conf?.bullish_pct, "var(--success)")}
            {pctBar("Bearish tilt", conf?.bearish_pct, "var(--danger)")}
            {pctBar("Regime fit", conf?.regime_pct, "var(--accent)")}
          </div>
        </div>

        {inst?.summary && (
          <div
            style={{
              padding: "12px 14px",
              borderRadius: 10,
              border: "1px solid rgba(255,255,255,0.08)",
              background: "rgba(0,0,0,0.25)",
              fontSize: "0.78rem",
              color: "var(--text-secondary)",
              lineHeight: 1.55,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, fontWeight: 800, fontSize: "0.62rem", color: "var(--accent)" }}>
              <Sparkles size={14} /> Institutional read (probabilistic)
            </div>
            {inst.summary}
            {inst.tags && inst.tags.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
                {inst.tags.map((t, i) => (
                  <span
                    key={`${t.id}-${i}`}
                    title={t.note}
                    style={{
                      padding: "6px 10px",
                      borderRadius: 8,
                      fontSize: "0.68rem",
                      border: "1px solid rgba(0,212,255,0.2)",
                      background: "rgba(0,212,255,0.06)",
                      color: "var(--text-primary)",
                    }}
                  >
                    {t.label}
                    {typeof t.likelihood === "number" && (
                      <span style={{ color: "var(--text-dim)", marginLeft: 6 }}>~{Math.round(t.likelihood * 100)}%</span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Session story timeline + prior-session context */}
      {(timeline.length > 0 || cross?.available || cross?.note) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {timeline.length > 0 && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(0,212,255,0.15)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <History size={16} color="var(--accent)" />
                <div>
                  <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>
                    SESSION STORY TIMELINE
                  </div>
                  <div style={{ fontSize: "0.68rem", color: "var(--text-dim)" }}>Chronological intelligence (this session, Redis-backed)</div>
                </div>
              </div>
              <div
                style={{
                  display: "flex",
                  gap: 10,
                  overflowX: "auto",
                  paddingBottom: 6,
                  scrollSnapType: "x mandatory",
                }}
              >
                {timeline.slice(0, 24).map((ev, i) => (
                  <div
                    key={`${ev.ts}-${i}`}
                    style={{
                      flex: "0 0 200px",
                      scrollSnapAlign: "start",
                      padding: "10px 12px",
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.08)",
                      background: "rgba(0,0,0,0.2)",
                    }}
                  >
                    <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--accent)" }}>{ev.ts_ist}</div>
                    <div style={{ fontSize: "0.72rem", fontWeight: 750, color: "var(--text-primary)", marginTop: 4, lineHeight: 1.35 }}>
                      {ev.headline?.slice(0, 120)}
                    </div>
                    <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", marginTop: 6 }}>
                      {ev.regime?.replace(/_/g, " ")}
                      {ev.dominant_shift ? ` · ${ev.dominant_shift.replace(/_/g, " ")}` : ""}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="glass" style={{ padding: 14, border: "1px solid rgba(148,163,184,0.15)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <Crosshair size={16} color="var(--warning)" />
              <div>
                <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>
                  CROSS-SESSION VS ANCHOR
                </div>
                <div style={{ fontSize: "0.68rem", color: "var(--text-dim)" }}>
                  {sess?.session_date_ist ? `Today ${sess.session_date_ist} (IST date)` : ""}
                </div>
              </div>
            </div>
            {cross?.available && cross.bullets && cross.bullets.length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.8rem", color: "var(--text-secondary)", lineHeight: 1.55 }}>
                {cross.bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            ) : (
              <p style={{ margin: 0, fontSize: "0.78rem", color: "var(--text-dim)", lineHeight: 1.5 }}>{cross?.note ?? "Compare unlocks when a prior-session anchor exists (after-hours snapshot)."}</p>
            )}
          </div>
        </div>
      )}

      {/* Flow strength + institutional intent radar */}
      {(flow || intent2?.intents?.length) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {flow && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(0,224,150,0.12)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Activity size={16} color="var(--success)" />
                <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>
                  FLOW STRENGTH / MOMENTUM
                </div>
              </div>
              <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)", marginBottom: 10 }}>
                Pressure: <strong style={{ color: "var(--text-primary)" }}>{flow.pressure_strength}</strong>
                {" · "}
                Trend: {flow.bullish_pressure_trend?.replace(/_/g, " ")}
              </div>
              {flow.scores && (
                <div style={{ marginBottom: 10 }}>
                  {pctBar("Acceleration", flow.scores.acceleration, "var(--accent)")}
                  {pctBar("Persistence", flow.scores.persistence, "var(--success)")}
                  {pctBar("Decay risk", flow.scores.decay_risk, "var(--danger)")}
                </div>
              )}
              {(flow.narratives ?? []).map((n, i) => (
                <p key={i} style={{ margin: "0 0 6px", fontSize: "0.76rem", color: "var(--text-secondary)" }}>
                  {n}
                </p>
              ))}
            </div>
          )}
          {intent2?.intents && intent2.intents.length > 0 && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(129,140,248,0.18)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <Radio size={16} color="#818cf8" />
                <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>
                  INSTITUTIONAL INTENT (PROBABILISTIC)
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {intent2.intents.slice(0, 8).map((it, i) => (
                  <div key={`${it.id}-${i}`}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", marginBottom: 4 }}>
                      <span style={{ color: "var(--text-secondary)" }}>{it.label}</span>
                      <span style={{ fontFamily: "monospace", color: "var(--accent)" }}>
                        ≤{it.confidence_cap}% cap · {it.probability_pct}%
                      </span>
                    </div>
                    <div style={{ height: 6, borderRadius: 4, background: "rgba(255,255,255,0.06)" }}>
                      <div
                        style={{
                          width: `${Math.min(100, it.probability_pct ?? 0)}%`,
                          height: "100%",
                          borderRadius: 4,
                          background: "linear-gradient(90deg, #818cf8, transparent)",
                        }}
                      />
                    </div>
                    {it.note && (
                      <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", marginTop: 4 }}>{it.note}</div>
                    )}
                  </div>
                ))}
              </div>
              {intent2.disclaimer && (
                <p style={{ margin: "12px 0 0", fontSize: "0.62rem", color: "var(--text-dim)", lineHeight: 1.45 }}>{intent2.disclaimer}</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Strike evolution strip */}
      {evo.length > 0 && (
        <div className="glass" style={{ padding: 14, border: "1px solid rgba(251,191,36,0.15)" }}>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 10, letterSpacing: 0.1 }}>
            STRIKE EVOLUTION MAP
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {evo.slice(0, 14).map((r, i) => (
              <div
                key={`${r.underlying}-${r.strike}-${i}`}
                style={{
                  padding: "8px 12px",
                  borderRadius: 10,
                  border: "1px solid rgba(255,255,255,0.08)",
                  background: "rgba(0,0,0,0.2)",
                  fontSize: "0.72rem",
                  maxWidth: 280,
                }}
              >
                <span style={{ color: "var(--accent)", fontWeight: 800, marginRight: 6 }}>{arrowSymbol(r.arrow)}</span>
                <strong style={{ color: "var(--text-primary)" }}>
                  {r.underlying}
                  {r.strike != null ? ` ${r.strike}` : ""}
                </strong>
                <div style={{ color: "var(--text-secondary)", marginTop: 4 }}>{r.short}</div>
                {r.strength_delta && (
                  <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", marginTop: 4 }}>{r.strength_delta}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Smart trading actions */}
      {smart &&
        ((smart.preferred_strategy_lines?.length ?? 0) > 0 ||
          (smart.avoid_lines?.length ?? 0) > 0 ||
          (smart.risk_context_lines?.length ?? 0) > 0) && (
          <div className="glass" style={{ padding: 14, border: "1px solid rgba(34,197,94,0.2)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <Target size={16} color="var(--success)" />
              <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.1 }}>
                SMART GUIDANCE ( CONTEXTUAL )
              </div>
            </div>
            <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", marginBottom: 10 }}>
              Breakout quality: <strong>{smart.breakout_quality}</strong> · Momentum quality:{" "}
              <strong>{smart.momentum_quality}</strong> · Trap hint: <strong>{smart.trap_probability_hint}</strong>
            </div>
            {smart.preferred_strategy_lines && smart.preferred_strategy_lines.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--success)", marginBottom: 4 }}>Preferred tone</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                  {smart.preferred_strategy_lines.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            )}
            {smart.avoid_lines && smart.avoid_lines.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--warning)", marginBottom: 4 }}>Avoid</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                  {smart.avoid_lines.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            )}
            {smart.risk_context_lines && smart.risk_context_lines.length > 0 && (
              <div>
                <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--danger)", marginBottom: 4 }}>Risk context</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                  {smart.risk_context_lines.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

      {/* What changed */}
      {(wc?.bullets?.length ?? 0) > 0 && (
        <div className="glass" style={{ padding: 16, border: "1px solid rgba(245,158,11,0.18)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <Zap size={16} color="var(--warning)" />
            <div>
              <div style={{ fontSize: "0.58rem", fontWeight: 800, letterSpacing: 0.1, color: "var(--text-dim)" }}>
                WHAT CHANGED
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>{wc?.window_label}</div>
            </div>
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.82rem", lineHeight: 1.65 }}>
            {wc!.bullets!.map((b, i) => (
              <li key={i} style={{ color: bulletToneColor(b.tone), marginBottom: 6 }}>
                {b.text}
              </li>
            ))}
          </ul>
          {wc?.persistence_note && (
            <p style={{ margin: "12px 0 0", fontSize: "0.68rem", color: "var(--text-dim)" }}>{wc.persistence_note}</p>
          )}
        </div>
      )}

      {/* Trading guidance */}
      {guidanceKeys.length > 0 && (
        <div>
          <div style={{ fontSize: "0.58rem", fontWeight: 800, letterSpacing: 0.1, color: "var(--text-dim)", marginBottom: 10, display: "flex", alignItems: "center", gap: 6 }}>
            <Gauge size={14} /> TRADING GUIDANCE
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
            {guidanceKeys.map((k) => {
              const g = data.guidance[k];
              return (
                <div
                  key={k}
                  className="glass"
                  style={{ padding: 14, border: "1px solid rgba(255,255,255,0.08)", background: "rgba(0,0,0,0.18)" }}
                >
                  <div style={{ fontWeight: 900, fontSize: "0.9rem", marginBottom: 10 }}>{k}</div>
                  <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                    <div>
                      <strong style={{ color: "var(--success)" }}>Bullish bias</strong> ideally above{" "}
                      <span style={{ fontFamily: "monospace", color: "var(--text-primary)" }}>
                        {g.bullish_above != null ? g.bullish_above : "—"}
                      </span>
                    </div>
                    <div style={{ marginTop: 6 }}>
                      <strong style={{ color: "var(--danger)" }}>Bearish bias</strong> ideally below{" "}
                      <span style={{ fontFamily: "monospace" }}>{g.bearish_below != null ? g.bearish_below : "—"}</span>
                    </div>
                    {g.avoid_trade_zone && g.avoid_trade_zone.length >= 2 && (
                      <div style={{ marginTop: 8, color: "var(--warning)" }}>
                        <Shield size={12} style={{ display: "inline", verticalAlign: "middle", marginRight: 4 }} />
                        Avoid / chop zone: {g.avoid_trade_zone[0]}–{g.avoid_trade_zone[1]}
                      </div>
                    )}
                    {g.trap_zone && g.trap_zone.length >= 2 && (
                      <div style={{ marginTop: 6, color: "var(--warning)" }}>
                        Trap / pin risk: {g.trap_zone[0]}–{g.trap_zone[1]}
                      </div>
                    )}
                    {g.breakout_trigger != null && (
                      <div style={{ marginTop: 6 }}>
                        <TrendingUp size={12} style={{ display: "inline", verticalAlign: "middle", marginRight: 4 }} />
                        Breakout watch above{" "}
                        <span style={{ fontFamily: "monospace" }}>{g.breakout_trigger}</span>
                      </div>
                    )}
                    {g.momentum_expansion_above != null && (
                      <div style={{ marginTop: 4, fontSize: "0.72rem" }}>
                        Momentum expansion possible above{" "}
                        <span style={{ fontFamily: "monospace", color: "var(--accent)" }}>{g.momentum_expansion_above}</span>
                      </div>
                    )}
                    {g.weak_structure_below != null && (
                      <div style={{ marginTop: 4, fontSize: "0.72rem", color: "var(--danger)" }}>
                        Weak structure risk below{" "}
                        <span style={{ fontFamily: "monospace" }}>{g.weak_structure_below}</span>
                      </div>
                    )}
                    {g.avoid_aggressive_shorts_above != null && (
                      <div style={{ marginTop: 6, fontSize: "0.72rem" }}>
                        Avoid aggressive shorts above{" "}
                        <span style={{ fontFamily: "monospace" }}>{g.avoid_aggressive_shorts_above}</span>
                      </div>
                    )}
                    {g.liquidity_sweep_risk && (
                      <div style={{ marginTop: 8, fontSize: "0.68rem", color: "var(--text-dim)" }}>
                        Liquidity sweep risk: <strong style={{ color: "var(--warning)" }}>{g.liquidity_sweep_risk}</strong>
                      </div>
                    )}
                    {g.structure_note && (
                      <div style={{ marginTop: 10, fontSize: "0.7rem", color: "var(--text-dim)" }}>{g.structure_note}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* OI shift feed + strike cards */}
      {(shifts.length > 0 || strikes.length > 0) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {shifts.length > 0 && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 10 }}>
                FLOW SHIFTS ( VS PRIOR SCAN )
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {shifts.slice(0, 10).map((s, i) => (
                  <div
                    key={`${s.type}-${s.strike}-${i}`}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 8,
                      border: "1px solid rgba(255,255,255,0.06)",
                      fontSize: "0.78rem",
                      color: "var(--text-secondary)",
                    }}
                  >
                    <span style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--accent)", marginRight: 8 }}>
                      {(s.type ?? "note").replace(/_/g, " ").toUpperCase()}
                    </span>
                    {s.text}
                  </div>
                ))}
              </div>
            </div>
          )}

          {strikes.length > 0 && (
            <div className="glass" style={{ padding: 14, border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", marginBottom: 10 }}>
                STRIKE INTELLIGENCE
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {strikes.slice(0, 8).map((c, i) => (
                  <div
                    key={`${c.underlying}-${c.strike}-${i}`}
                    style={{
                      padding: "10px 12px",
                      borderRadius: 10,
                      border:
                        c.tone === "structural"
                          ? "1px solid rgba(0,212,255,0.25)"
                          : "1px solid rgba(255,165,2,0.22)",
                      background: "rgba(255,255,255,0.02)",
                    }}
                  >
                    <div style={{ fontWeight: 850, fontSize: "0.82rem" }}>
                      {c.underlying} {c.strike}
                      {c.role && (
                        <span style={{ fontSize: "0.62rem", color: "var(--text-dim)", marginLeft: 8 }}>
                          ({c.role.replace(/_/g, " ")})
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)", marginTop: 4 }}>{c.detail}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Analyst detail — scan summaries */}
      {(data.summary_lines?.length ?? 0) > 0 && (
        <div className="glass" style={{ padding: 14, border: "1px solid rgba(255,255,255,0.06)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
            <Activity size={14} color="var(--text-dim)" />
            <span style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)" }}>SCAN NOTES</span>
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.8rem", color: "var(--text-secondary)", lineHeight: 1.55 }}>
            {data.summary_lines!.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
