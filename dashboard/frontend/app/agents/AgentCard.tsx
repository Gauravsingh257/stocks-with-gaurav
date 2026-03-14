"use client";
import { Clock, Play, RefreshCw } from "lucide-react";
import type { AgentStatus } from "./types";
import { AGENT_META, DEFAULT_META, statusDot, fmtDateTime, fmtTime } from "./types";
import { Bot } from "lucide-react";

export function AgentCard({
  agent,
  isRunning,
  progress,
  onRun,
}: {
  agent: AgentStatus;
  isRunning: boolean;
  progress: number;
  onRun: () => void;
}) {
  const meta   = AGENT_META[agent.name] || { icon: <Bot size={16} />, color: "var(--accent)", glow: "transparent" };
  const dot    = statusDot(agent.last_run?.status, isRunning);
  const lastTime = fmtDateTime(agent.last_run?.run_time);
  const nextTime = fmtTime(agent.next_run);

  return (
    <div
      className={isRunning ? "stat-card agent-card-running" : "stat-card"}
      style={{
        border: `1px solid ${meta.color}33`,
        borderTop: `3px solid ${meta.color}`,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        "--glow": meta.glow,
        transition: "box-shadow 0.3s",
      } as React.CSSProperties}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: meta.color }}>{meta.icon}</span>
          <span style={{ fontWeight: 700, fontSize: "0.88rem" }}>{agent.name}</span>
        </div>
        <span className="badge badge-neutral" style={{ fontSize: "0.68rem" }}>
          <Clock size={9} style={{ display: "inline", marginRight: 3 }} />
          {agent.schedule}
        </span>
      </div>

      {/* Status dot + label row */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.72rem" }}>
        <span style={{
          width: 7, height: 7, borderRadius: "50%",
          background: dot.color,
          display: "inline-block",
          animation: dot.pulse ? "pulse 1.4s ease infinite" : "none",
          boxShadow: dot.pulse ? `0 0 8px ${dot.color}` : "none",
        }} />
        <span style={{ color: dot.color, fontWeight: 600 }}>{dot.label}</span>
      </div>

      <p style={{ fontSize: "0.77rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: 0 }}>
        {agent.description}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: "0.72rem" }}>
        <div>
          <div style={{ color: "var(--text-secondary)", marginBottom: 2 }}>Last run</div>
          <div style={{ color: "var(--text-primary)" }}>{lastTime}</div>
        </div>
        <div>
          <div style={{ color: "var(--text-secondary)", marginBottom: 2 }}>Next run</div>
          <div style={{ color: "var(--text-primary)" }}>{nextTime}</div>
        </div>
      </div>

      {/* Progress bar (visible when running) */}
      {isRunning && (
        <div style={{ height: 3, background: "var(--bg-tertiary)", borderRadius: 2, overflow: "hidden" }}>
          <div
            className="progress-bar-fill"
            style={{ height: "100%", width: `${progress}%`, background: meta.color, borderRadius: 2 }}
          />
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <button
          className="btn-accent"
          onClick={onRun}
          disabled={isRunning}
          style={{
            fontSize: "0.74rem", padding: "4px 14px",
            opacity: isRunning ? 0.8 : 1,
            background: isRunning ? `${meta.color}22` : undefined,
            color: isRunning ? meta.color : undefined,
            border: isRunning ? `1px solid ${meta.color}44` : undefined,
          }}
        >
          {isRunning
            ? <><RefreshCw size={11} style={{ display: "inline", marginRight: 4, animation: "spin 1s linear infinite" }} />Running&hellip;</>
            : <><Play size={11} style={{ display: "inline", marginRight: 4 }} />Run Now</>}
        </button>
      </div>

      {agent.last_run?.summary && (
        <div style={{
          fontSize: "0.72rem", color: "var(--text-secondary)",
          background: "var(--bg-tertiary)", borderRadius: 6, padding: "6px 10px",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          borderLeft: `2px solid ${meta.color}44`,
        }}>
          {agent.last_run.summary}
        </div>
      )}
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="stat-card" style={{ opacity: 0.35, height: 200 }}>
      <div style={{ height: 20, background: "var(--bg-tertiary)", borderRadius: 4, width: "60%", marginBottom: 12 }} />
      <div style={{ height: 14, background: "var(--bg-tertiary)", borderRadius: 4, width: "90%", marginBottom: 8 }} />
      <div style={{ height: 14, background: "var(--bg-tertiary)", borderRadius: 4, width: "75%", marginBottom: 8 }} />
    </div>
  );
}
