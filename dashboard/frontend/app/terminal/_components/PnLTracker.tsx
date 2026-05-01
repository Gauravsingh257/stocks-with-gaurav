"use client";

/**
 * PnLTracker — compact daily performance strip.
 *
 * Shows: realized R, win/loss count, win rate, streak.
 * Mounts in the Hero bar of the terminal page.
 */

import type { DailyPnL } from "../_lib/useTerminalSummary";

interface Props {
  data: DailyPnL | null;
}

export default function PnLTracker({ data }: Props) {
  if (!data) return null;

  const { realized_r, wins, losses, win_rate, streak, total } = data;
  if (total === 0) return null;

  const rColor = realized_r > 0 ? "#00e096" : realized_r < 0 ? "#ff4757" : "#8899bb";
  const streakColor = streak > 0 ? "#00e096" : streak < 0 ? "#ff4757" : "#8899bb";
  const streakLabel = streak > 0 ? `+${streak} streak` : streak < 0 ? `${streak} streak` : "";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "6px 12px",
        borderRadius: 10,
        background: "rgba(0,0,0,0.25)",
        border: "1px solid var(--border)",
        flexWrap: "wrap",
      }}
    >
      <Stat label="Today" value={`${realized_r > 0 ? "+" : ""}${realized_r.toFixed(2)}R`} color={rColor} bold />
      <Divider />
      <Stat label="W/L" value={`${wins}/${losses}`} color="var(--text-primary)" />
      <Divider />
      <Stat label="WR" value={`${win_rate.toFixed(0)}%`} color={win_rate >= 60 ? "#00e096" : win_rate >= 40 ? "#ffa502" : "#ff4757"} />
      {streakLabel && (
        <>
          <Divider />
          <Stat label="" value={streakLabel} color={streakColor} />
        </>
      )}
    </div>
  );
}

function Stat({ label, value, color, bold }: { label: string; value: string; color: string; bold?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", lineHeight: 1.2 }}>
      {label && (
        <span style={{ fontSize: "0.5rem", color: "var(--text-dim)", letterSpacing: 0.8, textTransform: "uppercase" }}>{label}</span>
      )}
      <span style={{ fontSize: "0.72rem", fontWeight: bold ? 800 : 600, color, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{value}</span>
    </div>
  );
}

function Divider() {
  return <span style={{ width: 1, height: 20, background: "var(--border)", opacity: 0.5 }} />;
}
