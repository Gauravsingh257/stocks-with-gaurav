"use client";

import type { ResearchCoverageResponse } from "@/lib/api";

interface Props {
  coverage: ResearchCoverageResponse | null;
}

function pct(v: number) {
  return `${v.toFixed(1)}%`;
}

export function ResearchCoverageCard({ coverage }: Props) {
  if (!coverage) {
    return (
      <div className="glass" style={{ padding: 12, color: "var(--text-secondary)" }}>
        Coverage metrics unavailable.
      </div>
    );
  }

  const swing = coverage.latest.SWING;
  const longterm = coverage.latest.LONGTERM;
  const returned = coverage.returned_universe ?? coverage.available_universe;
  const dynamicCount = coverage.sources?.nse_dynamic_cache ?? 0;

  return (
    <div className="glass" style={{ padding: 14 }}>
      <div style={{ fontWeight: 600, marginBottom: 10 }}>Universe Coverage</div>
      <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: 10 }}>
        Requested: {coverage.target_universe} | Returned: {returned} | Available: {coverage.available_universe}
        {dynamicCount > 0 ? ` | NSE dynamic: ${dynamicCount}` : ""}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Swing Weekly Run</div>
          {swing ? (
            <div style={{ fontSize: "0.78rem", display: "grid", gap: 3 }}>
              {swing.run_time && (
                <div style={{ color: "var(--text-dim)" }}>Last run: {String(swing.run_time).slice(0, 19).replace("T", " ")}</div>
              )}
              <div>Scanned: {swing.universe_scanned}/{swing.universe_requested} ({pct(swing.coverage_pct)})</div>
              <div>Quality Passed: {swing.quality_passed}</div>
              <div>Ranked: {swing.ranked_candidates}</div>
              <div>Selected: {swing.selected_count}</div>
              {swing.selected_count === 0 && swing.ranked_candidates > 0 && (
                <div style={{ color: "var(--warning)", marginTop: 4 }}>
                  No valid setups found based on current market conditions. Try again later or adjust filters.
                </div>
              )}
            </div>
          ) : (
            <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>No run yet.</div>
          )}
        </div>
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Long-Term Weekly Run</div>
          {longterm ? (
            <div style={{ fontSize: "0.78rem", display: "grid", gap: 3 }}>
              {longterm.run_time && (
                <div style={{ color: "var(--text-dim)" }}>Last run: {String(longterm.run_time).slice(0, 19).replace("T", " ")}</div>
              )}
              <div>Scanned: {longterm.universe_scanned}/{longterm.universe_requested} ({pct(longterm.coverage_pct)})</div>
              <div>Quality Passed: {longterm.quality_passed}</div>
              <div>Ranked: {longterm.ranked_candidates}</div>
              <div>Selected: {longterm.selected_count}</div>
              {longterm.selected_count === 0 && longterm.ranked_candidates > 0 && (
                <div style={{ color: "var(--warning)", marginTop: 4 }}>
                  No valid setups found based on current market conditions. Try again later or adjust filters.
                </div>
              )}
            </div>
          ) : (
            <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>No run yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
