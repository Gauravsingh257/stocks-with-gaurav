"use client";

/**
 * DecisionBadge — Phase 4 Decision Engine output.
 *
 * Renders the trade action (STRONG BUY / BUY / WATCH / AVOID) with
 * a conviction pill. This is the headline output of the engine.
 */

import type { ActionLabel, ConvictionLevel } from "../_lib/opportunity";

interface Props {
  action: ActionLabel;
  conviction: ConvictionLevel;
  size?: "sm" | "md";
}

const ACTION_STYLE: Record<ActionLabel, { bg: string; fg: string; border: string; icon: string }> = {
  "STRONG BUY": {
    bg: "rgba(0, 224, 150, 0.18)",
    fg: "#00e096",
    border: "rgba(0, 224, 150, 0.55)",
    icon: "↑↑",
  },
  BUY: {
    bg: "rgba(0, 212, 255, 0.14)",
    fg: "#00d4ff",
    border: "rgba(0, 212, 255, 0.45)",
    icon: "↑",
  },
  WATCH: {
    bg: "rgba(255, 165, 2, 0.12)",
    fg: "#ffa502",
    border: "rgba(255, 165, 2, 0.40)",
    icon: "◎",
  },
  AVOID: {
    bg: "rgba(255, 71, 87, 0.12)",
    fg: "#ff4757",
    border: "rgba(255, 71, 87, 0.40)",
    icon: "✕",
  },
};

const CONVICTION_STYLE: Record<ConvictionLevel, { fg: string }> = {
  HIGH: { fg: "#00e096" },
  MEDIUM: { fg: "#ffa502" },
  LOW: { fg: "#8899bb" },
};

export default function DecisionBadge({ action, conviction, size = "md" }: Props) {
  const a = ACTION_STYLE[action] ?? ACTION_STYLE.WATCH;
  const c = CONVICTION_STYLE[conviction] ?? CONVICTION_STYLE.LOW;
  const isMd = size === "md";

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      {/* Action chip */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: isMd ? 5 : 3,
          padding: isMd ? "5px 10px" : "3px 7px",
          borderRadius: 999,
          background: a.bg,
          border: `1.5px solid ${a.border}`,
          color: a.fg,
          fontSize: isMd ? "0.72rem" : "0.6rem",
          fontWeight: 800,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          whiteSpace: "nowrap",
          userSelect: "none",
        }}
      >
        <span style={{ fontSize: isMd ? "0.85rem" : "0.72rem" }}>{a.icon}</span>
        {action}
      </span>

      {/* Conviction pill */}
      <span
        style={{
          fontSize: isMd ? "0.58rem" : "0.52rem",
          fontWeight: 700,
          letterSpacing: 0.8,
          color: c.fg,
          opacity: 0.85,
          textTransform: "uppercase",
        }}
      >
        {conviction}
      </span>
    </div>
  );
}
