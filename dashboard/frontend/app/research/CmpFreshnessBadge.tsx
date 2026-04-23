"use client";

import React from "react";

const SOURCE_LABEL: Record<string, string> = {
  ws_cache: "WS",
  kite_live: "Kite",
  yf_delayed: "yf",
  scan_snapshot: "snap",
  db_snapshot: "db",
  unknown: "?",
};

const SOURCE_COLOR: Record<string, string> = {
  ws_cache: "#10b981",     // green — live tick
  kite_live: "#10b981",    // green — fresh REST
  yf_delayed: "#f59e0b",   // amber — delayed
  scan_snapshot: "#6b7280", // grey — frozen at scan
  db_snapshot: "#6b7280",
  unknown: "#6b7280",
};

const SOURCE_TOOLTIP: Record<string, string> = {
  ws_cache: "Live WebSocket tick from engine cache",
  kite_live: "Just-fetched price from Kite REST",
  yf_delayed: "Yahoo Finance — delayed ~15 min",
  scan_snapshot: "Frozen at last research scan",
  db_snapshot: "Last value persisted to DB",
  unknown: "Source unknown",
};

function formatAge(ageSec: number | null | undefined): string {
  if (ageSec == null) return "";
  if (ageSec < 60) return `${ageSec}s`;
  if (ageSec < 3600) return `${Math.round(ageSec / 60)}m`;
  if (ageSec < 86400) return `${Math.round(ageSec / 3600)}h`;
  return `${Math.round(ageSec / 86400)}d`;
}

export function CmpFreshnessBadge({
  source,
  ageSec,
}: {
  source?: string | null;
  ageSec?: number | null;
}) {
  if (!source) return null;
  const key = source in SOURCE_LABEL ? source : "unknown";
  const label = SOURCE_LABEL[key];
  const color = SOURCE_COLOR[key];
  const tip = SOURCE_TOOLTIP[key];
  const ageStr = formatAge(ageSec);
  return (
    <span
      title={tip + (ageStr ? ` · age ${ageStr}` : "")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        marginLeft: 6,
        padding: "1px 5px",
        fontSize: "0.65rem",
        fontWeight: 600,
        color,
        background: `${color}1a`,
        borderRadius: 3,
        whiteSpace: "nowrap",
      }}
    >
      {label}
      {ageStr && <span style={{ opacity: 0.7 }}>·{ageStr}</span>}
    </span>
  );
}
