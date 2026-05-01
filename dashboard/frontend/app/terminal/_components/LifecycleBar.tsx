"use client";

/**
 * LifecycleBar — animated trade state machine bar.
 *
 * Visual pipeline: WAITING → APPROACHING → TRIGGERED → RUNNING → CLOSED
 *
 * Accepts a WatchStatus and maps it to the correct active step.
 */

import type { WatchStatus } from "../_lib/opportunity";

const STEPS: { label: string; statuses: WatchStatus[] }[] = [
  { label: "Waiting", statuses: ["Waiting"] },
  { label: "Approaching", statuses: ["Approaching", "Tapped"] },
  { label: "Triggered", statuses: ["Triggered"] },
  { label: "Running", statuses: ["Running"] },
  { label: "Closed", statuses: ["TargetHit", "StopHit"] },
];

function getActiveIndex(status: WatchStatus): number {
  for (let i = 0; i < STEPS.length; i++) {
    if (STEPS[i].statuses.includes(status)) return i;
  }
  return 0;
}

function getStepColor(stepIdx: number, activeIdx: number, status: WatchStatus): { dot: string; line: string; label: string } {
  const isClosed = status === "TargetHit" || status === "StopHit";
  if (stepIdx < activeIdx) {
    // Completed step
    return { dot: "#00d4ff", line: "#00d4ff", label: "#00d4ff" };
  }
  if (stepIdx === activeIdx) {
    // Active step
    if (isClosed) {
      const color = status === "TargetHit" ? "#00e096" : "#ff4757";
      return { dot: color, line: color, label: color };
    }
    return { dot: "#ffa502", line: "#ffa502", label: "#ffa502" };
  }
  // Future step
  return { dot: "#2a3344", line: "#1e2535", label: "#445577" };
}

interface Props {
  status: WatchStatus;
  compact?: boolean;
}

export default function LifecycleBar({ status, compact = false }: Props) {
  const activeIdx = getActiveIndex(status);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        width: "100%",
        gap: 0,
        padding: compact ? "6px 0" : "8px 0",
      }}
    >
      {STEPS.map((step, idx) => {
        const colors = getStepColor(idx, activeIdx, status);
        const isActive = idx === activeIdx;
        const isLast = idx === STEPS.length - 1;

        return (
          <div key={step.label} style={{ display: "flex", alignItems: "center", flex: isLast ? 0 : 1, minWidth: 0 }}>
            {/* Step node */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div
                style={{
                  width: isActive ? 10 : 7,
                  height: isActive ? 10 : 7,
                  borderRadius: "50%",
                  background: colors.dot,
                  border: `1.5px solid ${colors.dot}`,
                  boxShadow: isActive ? `0 0 8px ${colors.dot}88` : "none",
                  transition: "all 0.2s",
                  animation: isActive && !["TargetHit", "StopHit"].includes(status) ? "pulseStep 1.8s ease-in-out infinite" : "none",
                }}
              />
              {!compact && (
                <span
                  style={{
                    fontSize: "0.52rem",
                    fontWeight: isActive ? 700 : 500,
                    color: colors.label,
                    letterSpacing: 0.3,
                    whiteSpace: "nowrap",
                    textTransform: "uppercase",
                  }}
                >
                  {step.label}
                </span>
              )}
            </div>

            {/* Connector line */}
            {!isLast && (
              <div
                style={{
                  flex: 1,
                  height: 2,
                  background: idx < activeIdx ? "#00d4ff44" : "#1e2535",
                  borderRadius: 1,
                  margin: compact ? "0 3px" : "0 4px",
                  marginBottom: compact ? 0 : 14, // offset for labels below
                  transition: "background 0.3s",
                }}
              />
            )}
          </div>
        );
      })}

      <style>{`
        @keyframes pulseStep {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(1.25); }
        }
      `}</style>
    </div>
  );
}
