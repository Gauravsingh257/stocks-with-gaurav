"use client";

import { useEffect, useState } from "react";
import StockCard from "@/components/StockCard";
import { TradingViewStockWidget } from "@/components/TradingViewStockWidget";
import { api, type StockAnalysis } from "@/lib/api";

/**
 * The SMC half of /stock/<symbol>.
 *
 * This is a client component, but it is seeded from `initial` rather than always
 * fetching in an effect — so when the server's best-effort Tier 2 call succeeds,
 * React renders this whole subtree to HTML during SSR and Googlebot sees the real
 * analysis instead of a spinner. The effect only runs when the server came back
 * empty (yfinance cold/blocked, or over the 6s render budget), which keeps the
 * page useful for humans without ever making the crawler wait on it.
 */
export default function StockAnalysisPanel({
  symbol,
  initial,
}: {
  symbol: string;
  initial: StockAnalysis | null;
}) {
  const [analysis, setAnalysis] = useState<StockAnalysis | null>(initial);
  // Only "loading" when the server handed us nothing — otherwise first paint is final.
  const [loading, setLoading] = useState(initial === null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initial !== null || !symbol) return;
    let cancelled = false;
    // No setLoading(true) here: `loading` already starts as `initial === null`,
    // so a synchronous setState in the effect would only cascade a render.
    api
      .searchStock(symbol)
      .then((data) => {
        if (!cancelled) setAnalysis(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load analysis");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, initial]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1.25fr) minmax(300px, 0.75fr)",
          gap: 16,
        }}
        className="stock-analysis-grid"
      >
        <div className="glass" style={{ padding: 10, minHeight: 440 }}>
          <TradingViewStockWidget symbol={symbol} />
        </div>
        {analysis ? (
          <StockCard analysis={analysis} />
        ) : (
          <div className="glass" style={{ padding: 18, color: "var(--text-secondary)" }}>
            {loading ? "Loading SMC analysis…" : error ?? "SMC analysis unavailable right now."}
          </div>
        )}
      </div>

      {analysis && (
        <>
          <section className="glass" style={{ padding: 16, display: "grid", gap: 12 }}>
            <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>
              Smart Money Concepts read on {symbol}
            </h2>
            <p style={{ margin: 0, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              {analysis.reason}
            </p>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: 10,
              }}
            >
              <Detail label="Entry Zone" value={zoneText(analysis.entry_zone)} />
              <Detail
                label="Stop Loss"
                value={analysis.stop_loss ? `₹${analysis.stop_loss.toFixed(2)}` : "-"}
              />
              <Detail
                label="Target"
                value={analysis.target ? `₹${analysis.target.toFixed(2)}` : "-"}
              />
              <Detail label="Setup" value={analysis.setup_type.replace(/_/g, " ")} />
            </div>
          </section>

          <section className="glass" style={{ padding: 16, display: "grid", gap: 10 }}>
            <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>
              SMC zones detected on {symbol}
            </h2>
            {analysis.smc_zones.length > 0 ? (
              <div style={{ display: "grid", gap: 8 }}>
                {analysis.smc_zones.map((zone, index) => (
                  <div
                    key={`${zone.type}-${index}`}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 8,
                      border: "1px solid var(--border)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    <strong style={{ color: "var(--text-primary)" }}>{zone.type}</strong>{" "}
                    {zone.level != null
                      ? `₹${zone.level.toFixed(2)}`
                      : `₹${zone.bottom?.toFixed(2)} - ₹${zone.top?.toFixed(2)}`}
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: "var(--text-secondary)" }}>
                No active SMC zones detected on the daily chart.
              </div>
            )}
          </section>

          {analysis.criteria_not_met.length > 0 && (
            <section className="glass" style={{ padding: 16 }}>
              <h2 style={{ margin: "0 0 10px", fontSize: "1rem", fontWeight: 800 }}>
                Selection criteria not met
              </h2>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {analysis.criteria_not_met.map((item) => (
                  <span
                    key={item}
                    style={{
                      fontSize: "0.75rem",
                      padding: "4px 9px",
                      borderRadius: 999,
                      background: "rgba(245,158,11,0.1)",
                      border: "1px solid rgba(245,158,11,0.22)",
                      color: "var(--warning)",
                      fontWeight: 650,
                    }}
                  >
                    {item}
                  </span>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function zoneText(zone: StockAnalysis["entry_zone"]): string {
  if (!zone || zone.length < 2) return "-";
  return `₹${zone[0].toFixed(2)} - ₹${zone[1].toFixed(2)}`;
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "9px 11px",
        background: "rgba(255,255,255,0.02)",
      }}
    >
      <div
        style={{
          color: "var(--text-dim)",
          fontSize: "0.66rem",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </div>
      <div style={{ color: "var(--text-primary)", fontWeight: 800, marginTop: 3 }}>{value}</div>
    </div>
  );
}
