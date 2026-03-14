"use client";

import type { SwingIdea } from "@/lib/api";

interface Props {
  items: SwingIdea[];
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(2);
}

function signalList(signals: Record<string, string>) {
  return Object.values(signals || {}).filter(Boolean);
}

export function SwingIdeasTable({ items }: Props) {
  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>
        Swing Trade Opportunities
      </div>
      {items.length === 0 ? (
        <div style={{ padding: "24px", color: "var(--text-secondary)" }}>No swing ideas yet. Run the swing agent or wait for the weekly scan.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {["Symbol", "Entry", "Stop Loss", "Target 1", "Target 2", "Confidence", "Reasoning"].map(h => (
                  <th key={h} style={{ textAlign: "left", padding: "8px 12px", fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id} style={{ borderBottom: "1px solid var(--border-muted)" }}>
                    <td style={{ padding: "10px 12px", fontWeight: 600 }}>{item.symbol}</td>
                    <td style={{ padding: "10px 12px" }}>{fmt(item.entry_price)}</td>
                    <td style={{ padding: "10px 12px" }}>{fmt(item.stop_loss)}</td>
                    <td style={{ padding: "10px 12px" }}>{fmt(item.target_1)}</td>
                    <td style={{ padding: "10px 12px" }}>{fmt(item.target_2)}</td>
                    <td style={{ padding: "10px 12px", color: "#00ff88" }}>{item.confidence_score.toFixed(1)}%</td>
                    <td style={{ padding: "10px 12px", color: "var(--text-secondary)", maxWidth: 420 }}>
                      <details>
                        <summary style={{ cursor: "pointer", color: "var(--accent)" }}>View reasoning evidence</summary>
                        <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
                          <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>{item.reasoning_summary}</div>
                          <div style={{ fontSize: "0.78rem" }}>
                            <strong>Technical Factors</strong>
                            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                              {signalList(item.technical_signals).map((s, i) => <li key={`t-${item.id}-${i}`}>{s}</li>)}
                            </ul>
                          </div>
                          <div style={{ fontSize: "0.78rem" }}>
                            <strong>Fundamental Factors</strong>
                            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                              {signalList(item.fundamental_signals).map((s, i) => <li key={`f-${item.id}-${i}`}>{s}</li>)}
                            </ul>
                          </div>
                          <div style={{ fontSize: "0.78rem" }}>
                            <strong>Sentiment Factors</strong>
                            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                              {signalList(item.sentiment_signals).map((s, i) => <li key={`s-${item.id}-${i}`}>{s}</li>)}
                            </ul>
                          </div>
                        </div>
                      </details>
                    </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
