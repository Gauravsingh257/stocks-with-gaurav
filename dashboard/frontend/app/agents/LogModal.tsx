"use client";
import { useState } from "react";
import { Bot, X, Copy, CheckCheck } from "lucide-react";
import { AGENT_META, fmtDateTime, statusColor } from "./types";
import type { LogItem } from "./types";

function Section({ title, color, children }: { title: string; color: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.08em", color, marginBottom: 8, fontWeight: 600 }}>{title}</div>
      {children}
    </div>
  );
}

export function LogModal({ log, onClose }: { log: LogItem; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const meta = AGENT_META[log.agent_name] || { color: "var(--accent)", glow: "transparent" };
  const findings = log.findings || log.findings_json || [];
  const actions  = log.actions  || log.actions_json  || [];
  const metrics  = log.metrics  || log.metrics_json  || {};

  const fullJson = JSON.stringify({ summary: log.summary, findings, actions, metrics }, null, 2);

  const copyAll = () => {
    navigator.clipboard.writeText(fullJson).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "rgba(13,21,38,0.98)",
          border: `1px solid ${meta.color}44`,
          boxShadow: `0 0 40px ${meta.glow}`,
          borderRadius: 14, width: "min(720px, 96vw)", maxHeight: "80vh",
          display: "flex", flexDirection: "column", overflow: "hidden",
        }}
      >
        {/* Modal header */}
        <div style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ color: meta.color }}>{AGENT_META[log.agent_name]?.icon || <Bot size={16} />}</span>
            <div>
              <div style={{ fontWeight: 700, fontSize: "0.9rem" }}>{log.agent_name}</div>
              <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>{fmtDateTime(log.run_time)}</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: "0.75rem", fontWeight: 600, color: statusColor(log.status) }}>{log.status}</span>
            <button
              onClick={copyAll}
              style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: "0.72rem", color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 5 }}>
              {copied ? <CheckCheck size={11} color="#00ff88" /> : <Copy size={11} />}
              {copied ? "Copied!" : "Copy"}
            </button>
            <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-secondary)", padding: 4 }}>
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Modal body */}
        <div style={{ overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
          <Section title="Summary" color={meta.color}>
            <div style={{ fontSize: "0.83rem", lineHeight: 1.6, color: "var(--text-primary)" }}>{log.summary}</div>
          </Section>

          {metrics && Object.keys(metrics).length > 0 && (
            <Section title="Metrics" color={meta.color}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {Object.entries(metrics).map(([k, v]) => (
                  <div key={k} style={{ background: "var(--bg-tertiary)", borderRadius: 6, padding: "6px 12px", fontSize: "0.75rem" }}>
                    <span style={{ color: "var(--text-secondary)" }}>{k}: </span>
                    <span style={{ color: meta.color, fontWeight: 600 }}>{String(v)}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {Array.isArray(findings) && findings.length > 0 && (
            <Section title={`Findings (${findings.length})`} color={meta.color}>
              <pre style={{
                background: "var(--bg-tertiary)", borderRadius: 8, padding: 14,
                fontSize: "0.72rem", overflowX: "auto", lineHeight: 1.7,
                color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>
                {JSON.stringify(findings, null, 2)}
              </pre>
            </Section>
          )}

          {Array.isArray(actions) && actions.length > 0 && (
            <Section title={`Actions Proposed (${actions.length})`} color={meta.color}>
              <pre style={{
                background: "var(--bg-tertiary)", borderRadius: 8, padding: 14,
                fontSize: "0.72rem", overflowX: "auto", lineHeight: 1.7,
                color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>
                {JSON.stringify(actions, null, 2)}
              </pre>
            </Section>
          )}

          {(!findings || (findings as unknown[]).length === 0) && (!actions || (actions as unknown[]).length === 0) && (!metrics || Object.keys(metrics).length === 0) && (
            <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: "0.8rem", padding: "16px 0" }}>
              No findings or actions recorded for this run.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
