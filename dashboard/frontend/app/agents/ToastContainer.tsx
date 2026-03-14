"use client";
import { X } from "lucide-react";
import type { Toast } from "./types";

export function ToastContainer({ toasts, remove }: { toasts: Toast[]; remove: (id: number) => void }) {
  return (
    <div style={{
      position: "fixed", bottom: 24, right: 24, zIndex: 9999,
      display: "flex", flexDirection: "column", gap: 10, minWidth: 280,
    }}>
      {toasts.map(t => (
        <div key={t.id} style={{
          background: t.type === "success" ? "rgba(0,255,136,0.12)"
            : t.type === "error" ? "rgba(255,77,109,0.12)"
            : t.type === "warning" ? "rgba(255,215,0,0.12)"
            : "rgba(0,212,255,0.12)",
          border: `1px solid ${t.type === "success" ? "#00ff8844"
            : t.type === "error" ? "#ff4d6d44"
            : t.type === "warning" ? "#ffd70044"
            : "#00d4ff44"}`,
          borderLeft: `3px solid ${t.type === "success" ? "#00ff88"
            : t.type === "error" ? "#ff4d6d"
            : t.type === "warning" ? "#ffd700"
            : "#00d4ff"}`,
          borderRadius: 8, padding: "12px 14px",
          backdropFilter: "blur(10px)",
          display: "flex", alignItems: "flex-start", gap: 10,
          animation: "slideIn 0.25s ease",
        }}>
          <span style={{ fontSize: "1rem", lineHeight: 1 }}>
            {t.type === "success" ? "\u2705" : t.type === "error" ? "\u274C" : t.type === "warning" ? "\u26A0\uFE0F" : "\u2139\uFE0F"}
          </span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: "0.82rem", color: "var(--text-primary)" }}>{t.title}</div>
            {t.body && <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: 3 }}>{t.body}</div>}
          </div>
          <button onClick={() => remove(t.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-secondary)", padding: 2 }}>
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
