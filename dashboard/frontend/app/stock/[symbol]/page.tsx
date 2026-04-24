"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import StockCard from "@/components/StockCard";
import { api, type StockAnalysis } from "@/lib/api";

function zoneText(zone: StockAnalysis["entry_zone"]) {
  if (!zone || zone.length < 2) return "-";
  return `₹${zone[0].toFixed(2)} - ₹${zone[1].toFixed(2)}`;
}

export default function StockDetailPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(params.symbol || "").toUpperCase();
  const [analysis, setAnalysis] = useState<StockAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    api.searchStock(symbol)
      .then(setAnalysis)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load analysis"))
      .finally(() => setLoading(false));
  }, [symbol]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Link href="/research" style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--accent)", textDecoration: "none", fontSize: "0.82rem", fontWeight: 650 }}>
          <ArrowLeft size={15} /> Research
        </Link>
        <div style={{ width: 1, height: 20, background: "var(--border)" }} />
        <h1 style={{ margin: 0, fontSize: "1.45rem", fontWeight: 800 }}>NSE:{symbol}</h1>
      </div>

      {loading && <div className="glass" style={{ padding: 18, color: "var(--text-secondary)" }}>Loading stock analysis...</div>}
      {error && <div className="glass" style={{ padding: 18, color: "var(--danger)" }}>{error}</div>}

      {analysis && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(300px, 0.75fr)", gap: 16 }}>
            <div className="glass" style={{ padding: 10, minHeight: 440 }}>
              <iframe
                title={`${symbol} TradingView chart`}
                src={`https://www.tradingview.com/widgetembed/?symbol=NSE:${encodeURIComponent(symbol)}&interval=D&theme=dark&style=1`}
                style={{ width: "100%", height: 430, border: 0, borderRadius: 10 }}
                allowFullScreen
              />
            </div>
            <StockCard analysis={analysis} />
          </div>

          <div className="glass" style={{ padding: 16, display: "grid", gap: 12 }}>
            <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>Full Analysis</h2>
            <p style={{ margin: 0, color: "var(--text-secondary)", lineHeight: 1.6 }}>{analysis.reason}</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
              <Detail label="Entry Zone" value={zoneText(analysis.entry_zone)} />
              <Detail label="Stop Loss" value={analysis.stop_loss ? `₹${analysis.stop_loss.toFixed(2)}` : "-"} />
              <Detail label="Target" value={analysis.target ? `₹${analysis.target.toFixed(2)}` : "-"} />
              <Detail label="Setup" value={analysis.setup_type.replace(/_/g, " ")} />
            </div>
          </div>

          <div className="glass" style={{ padding: 16, display: "grid", gap: 10 }}>
            <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>SMC Zones</h2>
            {analysis.smc_zones.length > 0 ? (
              <div style={{ display: "grid", gap: 8 }}>
                {analysis.smc_zones.map((zone, index) => (
                  <div key={`${zone.type}-${index}`} style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                    <strong style={{ color: "var(--text-primary)" }}>{zone.type}</strong>{" "}
                    {zone.level != null ? `₹${zone.level.toFixed(2)}` : `₹${zone.bottom?.toFixed(2)} - ₹${zone.top?.toFixed(2)}`}
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: "var(--text-secondary)" }}>No active SMC zones detected on the daily chart.</div>
            )}
          </div>

          {analysis.criteria_not_met.length > 0 && (
            <div className="glass" style={{ padding: 16 }}>
              <h2 style={{ margin: "0 0 10px", fontSize: "1rem", fontWeight: 800 }}>Selection criteria not met:</h2>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {analysis.criteria_not_met.map((item) => (
                  <span key={item} style={{ fontSize: "0.75rem", padding: "4px 9px", borderRadius: 999, background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.22)", color: "var(--warning)", fontWeight: 650 }}>
                    {item}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "9px 11px", background: "rgba(255,255,255,0.02)" }}>
      <div style={{ color: "var(--text-dim)", fontSize: "0.66rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
      <div style={{ color: "var(--text-primary)", fontWeight: 800, marginTop: 3 }}>{value}</div>
    </div>
  );
}
