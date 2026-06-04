"use client";

import { useEffect, useState } from "react";
import { FlaskConical } from "lucide-react";
import { api, type AnchorShadowStatus } from "@/lib/api";

const COLOR: Record<string, string> = {
  READY: "#00e096",
  COLLECTING: "#5b9cf6",
  NOT_READY: "#ff4d6d",
  UNKNOWN: "#94a3b8",
};

/** Small at-a-glance Anchor10 shadow-validation status for the Research header.
 *  Reads the production source of truth (/api/research/anchor-shadow-status).
 *  Full C1–C5 breakdown lives in the Advanced tab chip. Renders nothing on error. */
export default function AnchorShadowHeaderBadge() {
  const [data, setData] = useState<AnchorShadowStatus | null>(null);

  useEffect(() => {
    let alive = true;
    api.anchorShadowStatus()
      .then((r) => { if (alive) setData(r ?? null); })
      .catch(() => { if (alive) setData(null); });
    return () => { alive = false; };
  }, []);

  if (!data || data.error) return null;
  const color = COLOR[data.overall] ?? COLOR.UNKNOWN;
  const label = data.overall === "NOT_READY" ? "NOT READY" : data.overall;

  return (
    <span
      title={`Anchor10 shadow validation: ${label} · ${data.session_count}/${data.sessions_required} sessions · ${data.recommendation}`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "6px 12px", borderRadius: 8, fontSize: "0.72rem", fontWeight: 700,
        border: `1px solid ${color}55`, background: `${color}14`, color,
      }}
    >
      <FlaskConical size={12} aria-hidden />
      Anchor10 · {label} · {data.session_count}/{data.sessions_required}
    </span>
  );
}
