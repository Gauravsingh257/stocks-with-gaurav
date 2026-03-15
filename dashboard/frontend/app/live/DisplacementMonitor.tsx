"use client";
/**
 * DisplacementMonitor — Momentum Shift Monitor
 *
 * Phase 2 Dashboard Panel: Shows early institutional displacement events
 * detected BEFORE CHoCH fires. Pipeline:
 *    Liquidity Sweep → [Displacement] → CHoCH → BOS → OB+FVG → Entry
 *
 * Endpoints used:
 *   GET /api/engine/displacement-events
 *   GET /api/engine/early-warning
 */

import { useEffect, useState, useCallback } from "react";
import { Activity, TrendingUp, TrendingDown, Zap, AlertTriangle, Clock } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface DisplacementEvent {
  symbol: string;
  timestamp: string;
  direction: "bullish" | "bearish";
  strength: "weak" | "medium" | "strong";
  created_fvg: boolean;
  atr_ratio: number;
  body_ratio: number;
  confidence: "low" | "medium" | "high";
  price: number;
  liquidity_context: "sweep_present" | "no_sweep" | string;
}

interface EarlyWarning {
  type: string;
  direction: "bullish" | "bearish";
  confidence: "low" | "medium" | "high";
  timestamp: string;
  liquidity: string;
  displacement?: DisplacementEvent;
}

interface EarlyWarningState {
  active_count: number;
  high_confidence: string[];
  med_confidence: string[];
  states: Record<string, EarlyWarning>;
}

// ─── Colour helpers ───────────────────────────────────────────────────────────

const CONF_COLOR: Record<string, string> = {
  high  : "#00e676",
  medium: "#ffd740",
  low   : "#90a4ae",
};

const STRENGTH_COLOR: Record<string, string> = {
  strong: "#ff5252",
  medium: "#ffd740",
  weak  : "#90a4ae",
};

const DIR_ICON = (dir: string) =>
  dir === "bullish" ? (
    <TrendingUp size={13} style={{ color: "#00e676" }} />
  ) : (
    <TrendingDown size={13} style={{ color: "#ff5252" }} />
  );

function fmt_time(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "--:--";
  }
}

function Badge({
  label,
  color,
  bg,
}: {
  label: string;
  color: string;
  bg: string;
}) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 7px",
        borderRadius: 4,
        fontSize: "0.65rem",
        fontWeight: 700,
        color,
        background: bg,
        textTransform: "uppercase",
        letterSpacing: "0.03em",
      }}
    >
      {label}
    </span>
  );
}

// ─── Early Warning Banner ─────────────────────────────────────────────────────

function EarlyWarningBanner({ state }: { state: EarlyWarningState }) {
  const { active_count, high_confidence, med_confidence, states } = state;

  if (active_count === 0) {
    return (
      <div
        style={{
          padding: "10px 14px",
          borderRadius: 8,
          background: "rgba(144,164,174,0.06)",
          border: "1px solid rgba(144,164,174,0.12)",
          color: "var(--text-dim)",
          fontSize: "0.78rem",
          textAlign: "center",
        }}
      >
        No early smart money activity detected
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {Object.entries(states).map(([symbol, w]) => (
        <div
          key={symbol}
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            background:
              w.confidence === "high"
                ? "rgba(0,230,118,0.07)"
                : "rgba(255,215,64,0.06)",
            border: `1px solid ${
              w.confidence === "high"
                ? "rgba(0,230,118,0.25)"
                : "rgba(255,215,64,0.2)"
            }`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Zap
              size={14}
              style={{ color: CONF_COLOR[w.confidence] }}
            />
            <span
              style={{
                fontWeight: 700,
                fontSize: "0.82rem",
                color: "var(--text-primary)",
              }}
            >
              {symbol.replace("NSE:", "")}
            </span>
            {DIR_ICON(w.direction)}
            <span
              style={{
                fontSize: "0.73rem",
                color:
                  w.direction === "bullish" ? "#00e676" : "#ff5252",
                fontWeight: 600,
              }}
            >
              {w.direction.toUpperCase()}
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {w.liquidity === "sweep_present" && (
              <Badge label="sweep" color="#ffd740" bg="rgba(255,215,64,0.12)" />
            )}
            <Badge
              label={w.confidence}
              color={CONF_COLOR[w.confidence]}
              bg={`${CONF_COLOR[w.confidence]}18`}
            />
            <span
              style={{ fontSize: "0.68rem", color: "var(--text-dim)" }}
            >
              {fmt_time(w.timestamp)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Event Row ────────────────────────────────────────────────────────────────

function EventRow({ ev, i }: { ev: DisplacementEvent; i: number }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "70px 95px 55px 55px 52px 52px 1fr 55px",
        gap: 6,
        padding: "7px 10px",
        borderRadius: 6,
        background:
          i % 2 === 0
            ? "rgba(255,255,255,0.02)"
            : "rgba(255,255,255,0.0)",
        alignItems: "center",
        fontSize: "0.72rem",
        color: "var(--text-secondary)",
      }}
    >
      {/* time */}
      <span style={{ color: "var(--text-dim)", fontFamily: "monospace" }}>
        {fmt_time(ev.timestamp)}
      </span>

      {/* symbol */}
      <span
        style={{ fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
      >
        {ev.symbol.replace("NSE:", "")}
      </span>

      {/* direction */}
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: 3,
          color: ev.direction === "bullish" ? "#00e676" : "#ff5252",
          fontWeight: 600,
        }}
      >
        {DIR_ICON(ev.direction)}
        {ev.direction === "bullish" ? "BULL" : "BEAR"}
      </span>

      {/* strength */}
      <span style={{ color: STRENGTH_COLOR[ev.strength], fontWeight: 600 }}>
        {ev.strength.toUpperCase()}
      </span>

      {/* ATR ratio */}
      <span style={{ fontFamily: "monospace" }}>×{ev.atr_ratio}</span>

      {/* body ratio */}
      <span style={{ fontFamily: "monospace" }}>
        {Math.round(ev.body_ratio * 100)}%
      </span>

      {/* FVG + sweep badges */}
      <div style={{ display: "flex", gap: 4 }}>
        {ev.created_fvg && (
          <Badge label="FVG" color="#448aff" bg="rgba(68,138,255,0.12)" />
        )}
        {ev.liquidity_context === "sweep_present" && (
          <Badge label="sweep" color="#ffd740" bg="rgba(255,215,64,0.12)" />
        )}
      </div>

      {/* confidence */}
      <Badge
        label={ev.confidence}
        color={CONF_COLOR[ev.confidence]}
        bg={`${CONF_COLOR[ev.confidence]}18`}
      />
    </div>
  );
}

// ─── Main Panel ───────────────────────────────────────────────────────────────

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function DisplacementMonitor() {
  const [events, setEvents]         = useState<DisplacementEvent[]>([]);
  const [earlyWarn, setEarlyWarn]   = useState<EarlyWarningState>({
    active_count: 0,
    high_confidence: [],
    med_confidence: [],
    states: {},
  });
  const [loading, setLoading]       = useState(true);
  const [lastUpdate, setLastUpdate] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const [evRes, ewRes] = await Promise.all([
        fetch(`${API}/api/engine/displacement-events?limit=30`),
        fetch(`${API}/api/engine/early-warning`),
      ]);

      if (evRes.ok) {
        const data = await evRes.json();
        setEvents(data.events ?? []);
      }

      if (ewRes.ok) {
        const data = await ewRes.json();
        setEarlyWarn(data);
      }

      setLastUpdate(new Date().toLocaleTimeString("en-IN"));
    } catch (_) {
      // silently fail — stale data shown
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      // Pause when tab is not visible to save API quota
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") {
        refresh();
      }
    }, 30_000); // was 15s — displacement events don't change faster than 30s
    return () => clearInterval(id);
  }, [refresh]);

  const highConf = events.filter((e) => e.confidence === "high");
  const withSweep = events.filter((e) => e.liquidity_context === "sweep_present");

  return (
    <div
      className="card"
      style={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      {/* ── Header ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: "rgba(255,215,64,0.12)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Activity size={16} style={{ color: "#ffd740" }} />
          </div>
          <div>
            <div
              style={{
                fontSize: "0.88rem",
                fontWeight: 700,
                color: "var(--text-primary)",
              }}
            >
              Momentum Shift Monitor
            </div>
            <div
              style={{
                fontSize: "0.7rem",
                color: "var(--text-dim)",
                marginTop: 1,
              }}
            >
              Early displacement detection · 30–40 min before CHoCH
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {earlyWarn.active_count > 0 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "3px 10px",
                borderRadius: 20,
                background: "rgba(255,215,64,0.12)",
                border: "1px solid rgba(255,215,64,0.3)",
              }}
            >
              <AlertTriangle size={11} style={{ color: "#ffd740" }} />
              <span
                style={{
                  fontSize: "0.7rem",
                  fontWeight: 700,
                  color: "#ffd740",
                }}
              >
                {earlyWarn.active_count} ACTIVE
              </span>
            </div>
          )}

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              color: "var(--text-dim)",
              fontSize: "0.68rem",
            }}
          >
            <Clock size={10} />
            {lastUpdate || "--"}
          </div>
        </div>
      </div>

      {/* ── Summary chips ── */}
      <div style={{ display: "flex", gap: 10 }}>
        <SummaryChip label="Events (today)" value={String(events.length)} />
        <SummaryChip
          label="High conf."
          value={String(highConf.length)}
          valueColor={highConf.length > 0 ? "#00e676" : undefined}
        />
        <SummaryChip
          label="With sweep"
          value={String(withSweep.length)}
          valueColor={withSweep.length > 0 ? "#ffd740" : undefined}
        />
        <SummaryChip
          label="Early warnings"
          value={String(earlyWarn.active_count)}
          valueColor={earlyWarn.active_count > 0 ? "#ffd740" : undefined}
        />
      </div>

      {/* ── Pipeline legend ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 12px",
          borderRadius: 6,
          background: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(255,255,255,0.06)",
          fontSize: "0.68rem",
          color: "var(--text-dim)",
          flexWrap: "wrap",
          rowGap: 4,
        }}
      >
        {[
          { label: "Liquidity", color: "#ffd740" },
          { label: "→ Displacement", color: "#ff9800" },
          { label: "→ CHoCH", color: "#90a4ae" },
          { label: "→ BOS", color: "#90a4ae" },
          { label: "→ OB+FVG", color: "#90a4ae" },
          { label: "→ Entry", color: "#00e676" },
        ].map((step) => (
          <span key={step.label} style={{ color: step.color, fontWeight: 600 }}>
            {step.label}
          </span>
        ))}
      </div>

      {/* ── Early Warning Banner ── */}
      <div>
        <div
          style={{
            fontSize: "0.72rem",
            fontWeight: 700,
            color: "var(--text-secondary)",
            marginBottom: 6,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Early Smart Money Activity
        </div>
        <EarlyWarningBanner state={earlyWarn} />
      </div>

      {/* ── Event table ── */}
      <div className="w-full overflow-x-auto min-w-0">
        <div
          style={{
            fontSize: "0.72rem",
            fontWeight: 700,
            color: "var(--text-secondary)",
            marginBottom: 6,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Recent Displacement Events
        </div>

        {/* Table header */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "70px 95px 55px 55px 52px 52px 1fr 55px",
            gap: 6,
            padding: "4px 10px",
            fontSize: "0.63rem",
            fontWeight: 700,
            color: "var(--text-dim)",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          <span>Time</span>
          <span>Symbol</span>
          <span>Dir</span>
          <span>Strength</span>
          <span>ATR×</span>
          <span>Body%</span>
          <span>Tags</span>
          <span>Conf</span>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            maxHeight: 280,
            overflowY: "auto",
          }}
        >
          {loading ? (
            <LoadingRow />
          ) : events.length === 0 ? (
            <EmptyRow label="No displacement events detected today" />
          ) : (
            events.map((ev, i) => <EventRow key={i} ev={ev} i={i} />)
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Mini helpers ─────────────────────────────────────────────────────────────

function SummaryChip({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div
      style={{
        padding: "6px 12px",
        borderRadius: 8,
        background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.08)",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minWidth: 70,
      }}
    >
      <span
        style={{
          fontSize: "1rem",
          fontWeight: 700,
          color: valueColor ?? "var(--text-primary)",
        }}
      >
        {value}
      </span>
      <span style={{ fontSize: "0.65rem", color: "var(--text-dim)" }}>
        {label}
      </span>
    </div>
  );
}

function LoadingRow() {
  return (
    <div
      style={{
        padding: "20px",
        textAlign: "center",
        color: "var(--text-dim)",
        fontSize: "0.78rem",
      }}
    >
      <div
        style={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          border: "2px solid var(--accent)",
          borderTopColor: "transparent",
          animation: "spin 0.8s linear infinite",
          margin: "0 auto 8px",
        }}
      />
      Loading displacement events…
    </div>
  );
}

function EmptyRow({ label }: { label: string }) {
  return (
    <div
      style={{
        padding: "16px",
        textAlign: "center",
        color: "var(--text-dim)",
        fontSize: "0.78rem",
      }}
    >
      {label}
    </div>
  );
}
