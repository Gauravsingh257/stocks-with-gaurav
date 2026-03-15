"use client";
/**
 * MarketStatePanel — Real-Time Market State Transition Display
 *
 * Shows the current market state (BULLISH_REVERSAL / BEARISH_REVERSAL /
 * TREND_CONTINUATION / RANGE) with triggering events, scoring breakdown,
 * and transition timeline.
 */
import { Activity, Zap, TrendingUp, TrendingDown, Minus, ArrowRightLeft } from "lucide-react";
import type { MarketState, MarketStateEvent } from "./types";

const STATE_CONFIG: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  BULLISH_REVERSAL: {
    label: "BULLISH REVERSAL",
    color: "#00e676",
    bg: "rgba(0, 230, 118, 0.08)",
    icon: <TrendingUp size={18} />,
  },
  BEARISH_REVERSAL: {
    label: "BEARISH REVERSAL",
    color: "#ff5252",
    bg: "rgba(255, 82, 82, 0.08)",
    icon: <TrendingDown size={18} />,
  },
  TREND_CONTINUATION: {
    label: "TREND CONTINUATION",
    color: "#448aff",
    bg: "rgba(68, 138, 255, 0.08)",
    icon: <ArrowRightLeft size={18} />,
  },
  RANGE: {
    label: "RANGE / NEUTRAL",
    color: "#ffd740",
    bg: "rgba(255, 215, 64, 0.08)",
    icon: <Minus size={18} />,
  },
};

const EVENT_ICONS: Record<string, string> = {
  CHOCH: "🔄",
  BOS: "📐",
  LIQUIDITY_SWEEP: "🧹",
  DISPLACEMENT: "💥",
  SHORT_COVERING: "🔥",
  PE_SUPPORT_WALL: "🛡",
  CE_RESISTANCE_WALL: "🧱",
  FVG_ALIGNMENT: "📊",
  HIGHER_LOWS: "📈",
  LOWER_HIGHS: "📉",
  PRICE_ABOVE_VWAP: "⬆️",
  PRICE_BELOW_VWAP: "⬇️",
};

interface Props {
  marketState: MarketState | undefined;
}

export function MarketStatePanel({ marketState }: Props) {
  if (!marketState) {
    return (
      <div style={{
        background: "var(--card-bg)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        padding: "20px 24px",
        textAlign: "center",
        color: "var(--text-dim)",
        fontSize: "0.8rem",
      }}>
        <Activity size={18} style={{ marginBottom: 6 }} />
        <div>Market State Engine — Awaiting data</div>
      </div>
    );
  }

  const { state, prev_state, confidence, events, bull_score, bear_score, net, transition_time } = marketState;
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.RANGE;
  const total = bull_score + bear_score || 1;
  const bullPct = Math.round((bull_score / total) * 100);
  const bearPct = 100 - bullPct;

  // Separate bull and bear events
  const bullEvents = events.filter(e => e.direction === "BULL");
  const bearEvents = events.filter(e => e.direction === "BEAR");

  return (
    <div style={{
      background: "var(--card-bg)",
      border: "1px solid var(--border)",
      borderRadius: 12,
      overflow: "hidden",
    }}>
      {/* Accent top bar */}
      <div style={{ height: 3, background: cfg.color }} />

      <div style={{ padding: "16px 20px" }}>
        {/* Header row */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ color: cfg.color, display: "flex", alignItems: "center" }}>
              {cfg.icon}
            </div>
            <span style={{ fontSize: "0.95rem", fontWeight: 700, color: cfg.color }}>
              {cfg.label}
            </span>
            {confidence > 0 && (
              <span style={{
                fontSize: "0.65rem",
                background: cfg.bg,
                color: cfg.color,
                padding: "2px 8px",
                borderRadius: 8,
                fontWeight: 600,
              }}>
                {confidence}/10
              </span>
            )}
          </div>

          {prev_state && prev_state !== state && (
            <div style={{
              fontSize: "0.65rem",
              color: "var(--text-dim)",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}>
              <Zap size={10} color={cfg.color} />
              from {(STATE_CONFIG[prev_state]?.label || prev_state).split(" ")[0]}
              {transition_time && (
                <span style={{ marginLeft: 4 }}>
                  @ {new Date(transition_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Score bar */}
        <div style={{ marginBottom: 14 }}>
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: "0.65rem",
            fontWeight: 600,
            color: "var(--text-secondary)",
            marginBottom: 4,
          }}>
            <span style={{ color: "#00e676" }}>BULL {bull_score}</span>
            <span style={{ color: "var(--text-dim)", fontSize: "0.6rem" }}>NET: {net > 0 ? "+" : ""}{net}</span>
            <span style={{ color: "#ff5252" }}>BEAR {bear_score}</span>
          </div>
          <div style={{
            display: "flex",
            height: 6,
            borderRadius: 3,
            overflow: "hidden",
            background: "var(--bg-secondary)",
          }}>
            <div style={{
              width: `${bullPct}%`,
              background: "linear-gradient(90deg, #00e676, #69f0ae)",
              borderRadius: "3px 0 0 3px",
              transition: "width 0.5s ease",
            }} />
            <div style={{
              width: `${bearPct}%`,
              background: "linear-gradient(90deg, #ff8a80, #ff5252)",
              borderRadius: "0 3px 3px 0",
              transition: "width 0.5s ease",
            }} />
          </div>
        </div>

        {/* Events grid */}
        {events.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {/* Bull events */}
            <div>
              <div style={{ fontSize: "0.6rem", color: "#69f0ae", fontWeight: 600, marginBottom: 4, letterSpacing: "0.05em" }}>
                BULLISH TRIGGERS
              </div>
              {bullEvents.length === 0 ? (
                <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", fontStyle: "italic" }}>None active</div>
              ) : (
                bullEvents.map((ev, i) => (
                  <EventPill key={`b-${i}`} event={ev} />
                ))
              )}
            </div>

            {/* Bear events */}
            <div>
              <div style={{ fontSize: "0.6rem", color: "#ff8a80", fontWeight: 600, marginBottom: 4, letterSpacing: "0.05em" }}>
                BEARISH TRIGGERS
              </div>
              {bearEvents.length === 0 ? (
                <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", fontStyle: "italic" }}>None active</div>
              ) : (
                bearEvents.map((ev, i) => (
                  <EventPill key={`r-${i}`} event={ev} />
                ))
              )}
            </div>
          </div>
        )}

        {events.length === 0 && (
          <div style={{ textAlign: "center", fontSize: "0.75rem", color: "var(--text-dim)", padding: "8px 0" }}>
            No structural events detected yet
          </div>
        )}
      </div>
    </div>
  );
}

function EventPill({ event }: { event: MarketStateEvent }) {
  const icon = EVENT_ICONS[event.type] || "•";
  const isBull = event.direction === "BULL";
  const color = isBull ? "#69f0ae" : "#ff8a80";
  const bg = isBull ? "rgba(0, 230, 118, 0.06)" : "rgba(255, 82, 82, 0.06)";

  return (
    <div style={{
      display: "flex",
      alignItems: "flex-start",
      gap: 5,
      padding: "3px 6px",
      borderRadius: 6,
      background: bg,
      marginBottom: 3,
      fontSize: "0.68rem",
      lineHeight: 1.3,
    }}>
      <span style={{ flexShrink: 0 }}>{icon}</span>
      <div>
        <span style={{ fontWeight: 600, color }}>{event.type.replace(/_/g, " ")}</span>
        {event.weight > 1 && (
          <span style={{ color: "var(--text-dim)", fontSize: "0.58rem", marginLeft: 3 }}>
            +{event.weight}
          </span>
        )}
        {event.detail && (
          <div style={{ color: "var(--text-dim)", fontSize: "0.62rem", marginTop: 1 }}>
            {event.detail}
          </div>
        )}
      </div>
    </div>
  );
}
