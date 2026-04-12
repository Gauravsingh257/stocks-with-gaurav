"use client";

export function pnlColor(v: number) {
  return v >= 0 ? "var(--success)" : "var(--danger)";
}

const STATUS_MAP: Record<string, { bg: string; text: string; label: string }> = {
  RUNNING:    { bg: "rgba(91,156,246,0.15)", text: "#5b9cf6", label: "Live" },
  TARGET_HIT: { bg: "rgba(0,209,140,0.15)",  text: "#00d18c", label: "Target ✓" },
  STOP_HIT:   { bg: "rgba(255,77,77,0.15)",   text: "#ff4d4d", label: "SL Hit" },
  PENDING:    { bg: "rgba(255,200,0,0.12)",   text: "#ffc800", label: "Pending" },
};

export function StatusBadge({ status }: { status: string }) {
  const c = STATUS_MAP[status] ?? STATUS_MAP["PENDING"];
  return (
    <span style={{
      padding: "2px 9px", borderRadius: 20, fontSize: "0.7rem", fontWeight: 700,
      background: c.bg, color: c.text,
    }}>{c.label}</span>
  );
}
