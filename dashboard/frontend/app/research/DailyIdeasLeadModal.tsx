"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { api } from "@/lib/api";

export function DailyIdeasLeadModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "err">("idle");
  const [msg, setMsg] = useState<string | null>(null);

  if (!open) return null;

  const submit = async () => {
    const e = email.trim();
    if (!e || !e.includes("@")) {
      setMsg("Enter a valid email.");
      return;
    }
    setStatus("loading");
    setMsg(null);
    try {
      await api.submitResearchEmailLead(e);
      setStatus("done");
      setMsg("You’re on the list. We’ll reach out when daily digests go live.");
    } catch (err) {
      setStatus("err");
      setMsg(err instanceof Error ? err.message : "Could not save — try again later.");
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="lead-modal-title"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 80,
        background: "rgba(0,0,0,0.55)",
        display: "grid",
        placeItems: "center",
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        className="glass"
        style={{
          maxWidth: 420,
          width: "100%",
          padding: 22,
          borderRadius: 14,
          border: "1px solid rgba(0,212,255,0.2)",
          boxShadow: "0 24px 60px rgba(0,0,0,0.45)",
        }}
        onClick={(ev) => ev.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 12 }}>
          <div>
            <h2 id="lead-modal-title" className="m-0 text-lg font-bold">Get daily stock ideas</h2>
            <p style={{ margin: "6px 0 0", color: "var(--text-secondary)", fontSize: "0.82rem", lineHeight: 1.5 }}>
              Leave your email for SMC + fundamentals digests and major scan updates. No spam — unsubscribe anytime when we wire the preference center.
            </p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} style={{ background: "none", border: "none", color: "var(--text-dim)", cursor: "pointer", padding: 4 }}>
            <X size={20} />
          </button>
        </div>
        <label style={{ display: "grid", gap: 6, marginBottom: 12 }}>
          <span style={{ fontSize: "0.72rem", color: "var(--text-dim)", fontWeight: 650 }}>Email</span>
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(ev) => setEmail(ev.target.value)}
            className="input-dark"
            placeholder="you@example.com"
            style={{ width: "100%", padding: "10px 12px", borderRadius: 8, fontSize: "0.88rem" }}
            disabled={status === "loading" || status === "done"}
          />
        </label>
        {msg && (
          <p style={{ margin: "0 0 12px", fontSize: "0.8rem", color: status === "err" ? "var(--danger)" : "var(--success)" }}>
            {msg}
          </p>
        )}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button type="button" onClick={onClose} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontWeight: 650 }}>
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={status === "loading" || status === "done"}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              border: "1px solid rgba(0,212,255,0.35)",
              background: "rgba(0,212,255,0.14)",
              color: "var(--accent)",
              fontWeight: 800,
              cursor: status === "loading" ? "wait" : "pointer",
              opacity: status === "done" ? 0.6 : 1,
            }}
          >
            {status === "loading" ? "Saving…" : status === "done" ? "Saved" : "Notify me"}
          </button>
        </div>
      </div>
    </div>
  );
}
