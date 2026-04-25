"use client";

import type { CSSProperties } from "react";
import type { LayerReportResponse, LayerReportRow } from "@/lib/api";

interface Props {
  report: LayerReportResponse | null;
}

function passLabel(value: number | boolean | undefined) {
  return value ? "PASS" : "FAIL";
}

function passStyle(value: number | boolean | undefined): CSSProperties {
  return {
    fontSize: "0.66rem",
    fontWeight: 800,
    padding: "2px 7px",
    borderRadius: 6,
    color: value ? "#00d18c" : "#ff6b6b",
    background: value ? "rgba(0,209,140,0.08)" : "rgba(255,107,107,0.08)",
    border: value ? "1px solid rgba(0,209,140,0.18)" : "1px solid rgba(255,107,107,0.18)",
  };
}

function StockLayerRow({ row }: { row: LayerReportRow }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(90px, 1.1fr) repeat(3, 58px) minmax(120px, 2fr)",
        gap: 8,
        alignItems: "center",
        padding: "7px 0",
        borderTop: "1px solid var(--border)",
        fontSize: "0.74rem",
      }}
    >
      <div>
        <div style={{ fontWeight: 750, color: "var(--text-primary)" }}>{row.symbol.replace("NSE:", "")}</div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.66rem" }}>Score {Number(row.confidence || 0).toFixed(1)}</div>
      </div>
      <span style={passStyle(row.layer1_pass)}>{passLabel(row.layer1_pass)}</span>
      <span style={passStyle(row.layer2_pass)}>{passLabel(row.layer2_pass)}</span>
      <span style={passStyle(row.layer3_pass)}>{passLabel(row.layer3_pass)}</span>
      <div style={{ color: row.rejection_reason?.length ? "var(--warning)" : "var(--text-secondary)", lineHeight: 1.35 }}>
        {row.rejection_reason?.length ? row.rejection_reason.join(" · ") : "selected"}
      </div>
    </div>
  );
}

export function ResearchLayerDebugPanel({ report }: Props) {
  if (!report?.available || !report.funnel) {
    return (
      <div className="glass" style={{ padding: 14, color: "var(--text-secondary)", fontSize: "0.8rem" }}>
        Layer validation log not available yet. Run validation to populate the audit trail.
      </div>
    );
  }

  const funnel = report.funnel;
  const coverage = report.coverage || {};
  const sample = report.sample || [];
  const rejectionCounts = Object.entries(report.rejection_counts || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);

  return (
    <div className="glass" style={{ padding: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
        <div>
          <div style={{ fontWeight: 750 }}>Layer Validation Debug</div>
          <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginTop: 2 }}>
            {report.horizon} · scan {report.scan_id} · {report.created_at ? String(report.created_at).slice(0, 19).replace("T", " ") : "latest"}
          </div>
        </div>
        <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)", textAlign: "right" }}>
          Coverage {coverage.scanned ?? 0}/{coverage.total_universe ?? coverage.scanned ?? 0}
          {typeof coverage.coverage_percent === "number" ? ` (${coverage.coverage_percent.toFixed(1)}%)` : ""}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 8, marginBottom: 12 }}>
        {[
          ["Total", funnel.total],
          ["L1 Discovery", funnel.layer1_pass],
          ["L2 Quality", funnel.layer2_pass],
          ["L3 SMC", funnel.layer3_pass],
          ["Selected", funnel.final_selected],
        ].map(([label, value]) => (
          <div key={String(label)} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 9 }}>
            <div style={{ color: "var(--text-dim)", fontSize: "0.68rem" }}>{label}</div>
            <div style={{ fontWeight: 800, fontSize: "1rem" }}>{value}</div>
          </div>
        ))}
      </div>

      {rejectionCounts.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
          {rejectionCounts.map(([reason, count]) => (
            <span key={reason} style={{ fontSize: "0.7rem", padding: "3px 8px", borderRadius: 999, color: "var(--warning)", background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.18)" }}>
              {reason}: {count}
            </span>
          ))}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(90px, 1.1fr) repeat(3, 58px) minmax(120px, 2fr)", gap: 8, color: "var(--text-dim)", fontSize: "0.66rem", fontWeight: 750, paddingBottom: 5 }}>
        <span>Stock</span><span>L1</span><span>L2</span><span>L3</span><span>Reason</span>
      </div>
      {sample.slice(0, 12).map((row) => <StockLayerRow key={`${row.id}-${row.symbol}`} row={row} />)}
    </div>
  );
}
