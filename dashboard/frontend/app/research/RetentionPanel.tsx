"use client";

import Link from "next/link";
import { Bell, Clock, ListChecks, Star } from "lucide-react";

export function RetentionPanel({
  hasIdeas,
  hasPortfolio,
}: {
  hasIdeas: boolean;
  hasPortfolio: boolean;
}) {
  const steps = [
    { icon: <Clock size={14} />, label: "Check fresh scan", done: hasIdeas },
    { icon: <Star size={14} />, label: "Shortlist top conviction", done: hasIdeas },
    { icon: <Bell size={14} />, label: "Track entry zone", done: hasPortfolio },
  ];

  return (
    <div className="glass" style={{ padding: 16, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 850 }}>
            <ListChecks size={16} color="var(--accent)" /> Daily Research Routine
          </div>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>
            Retention loop: scan once, shortlist, then monitor entry zones instead of checking random stocks.
          </p>
        </div>
        <Link
          href="/watchlist"
          style={{
            textDecoration: "none",
            padding: "7px 12px",
            borderRadius: 8,
            border: "1px solid rgba(0,212,255,0.28)",
            background: "rgba(0,212,255,0.08)",
            color: "var(--accent)",
            fontSize: "0.75rem",
            fontWeight: 800,
          }}
        >
          Open Watchlist
        </Link>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
        {steps.map((step) => (
          <div key={step.label} style={{ display: "flex", gap: 9, alignItems: "center", padding: "9px 10px", borderRadius: 9, border: "1px solid var(--border)", background: step.done ? "rgba(0,224,150,0.07)" : "rgba(255,255,255,0.025)" }}>
            <span style={{ display: "grid", placeItems: "center", width: 26, height: 26, borderRadius: 999, background: step.done ? "rgba(0,224,150,0.14)" : "rgba(148,163,184,0.12)", color: step.done ? "var(--success)" : "var(--text-secondary)" }}>
              {step.icon}
            </span>
            <div style={{ fontSize: "0.78rem", fontWeight: 750, color: step.done ? "var(--text-primary)" : "var(--text-secondary)" }}>{step.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
