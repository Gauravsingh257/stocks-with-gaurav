"use client";

import StockCard from "@/components/StockCard";
import type { LongTermIdea, StockAnalysis, SwingIdea } from "@/lib/api";

function recommendation(score: number): "Strong Buy" | "Watchlist" | "Avoid" {
  if (score >= 75) return "Strong Buy";
  if (score >= 50) return "Watchlist";
  return "Avoid";
}

function swingToAnalysis(item: SwingIdea): StockAnalysis {
  return {
    symbol: item.symbol.replace("NSE:", ""),
    name: item.symbol.replace("NSE:", ""),
    exchange: "NSE",
    cmp: item.scan_cmp ?? null,
    cmp_source: item.cmp_source ?? undefined,
    cmp_age_sec: item.cmp_age_sec ?? null,
    entry_zone: [Number((item.entry_price * 0.995).toFixed(2)), Number((item.entry_price * 1.005).toFixed(2))],
    stop_loss: item.stop_loss,
    target: item.target_2 ?? item.target_1,
    risk_reward: item.risk_reward,
    confidence_score: item.confidence_score,
    setup_type: item.setup,
    horizon: "SWING",
    recommendation: recommendation(item.confidence_score),
    reason: item.reasoning_summary || "SMC setup with defined entry, stop loss, target, and risk/reward.",
    criteria_not_met: [],
    smc_zones: [],
    fundamentals: {
      pe_ratio: item.pe_ratio,
      roe_pct: item.roe_pct,
      market_cap_cr: item.market_cap_cr,
      sector: item.sector,
    },
    updated_at: item.signals_updated_at || item.created_at,
  };
}

function longTermToAnalysis(item: LongTermIdea): StockAnalysis {
  const entryZone = Array.isArray(item.entry_zone) && item.entry_zone.length >= 2
    ? [item.entry_zone[0], item.entry_zone[1]]
    : [Number((item.entry_price * 0.98).toFixed(2)), item.entry_price];
  return {
    symbol: item.symbol.replace("NSE:", ""),
    name: item.symbol.replace("NSE:", ""),
    exchange: "NSE",
    cmp: item.scan_cmp ?? null,
    cmp_source: item.cmp_source ?? undefined,
    cmp_age_sec: item.cmp_age_sec ?? null,
    entry_zone: entryZone,
    stop_loss: item.stop_loss,
    target: item.long_term_target,
    risk_reward: item.risk_reward,
    confidence_score: item.confidence_score,
    setup_type: item.setup,
    horizon: "LONGTERM",
    recommendation: recommendation(item.confidence_score),
    reason: item.reasoning_summary || item.long_term_thesis || "Long-term setup with defined entry zone and thesis.",
    criteria_not_met: [],
    smc_zones: [],
    fundamentals: {
      pe_ratio: item.pe_ratio,
      roe_pct: item.roe_pct,
      market_cap_cr: item.market_cap_cr,
      sector: item.sector,
    },
    updated_at: item.signals_updated_at || item.created_at,
  };
}

export function TopIdeas({ swing, longterm }: { swing: SwingIdea[]; longterm: LongTermIdea[] }) {
  const items = [
    ...swing.map(swingToAnalysis),
    ...longterm.map(longTermToAnalysis),
  ]
    .sort((a, b) => {
      const conf = b.confidence_score - a.confidence_score;
      if (conf !== 0) return conf;
      return (b.risk_reward || 0) - (a.risk_reward || 0);
    })
    .slice(0, 3);

  if (items.length === 0) {
    return null;
  }

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <div style={{ width: 4, height: 24, borderRadius: 2, background: "var(--success)" }} />
        <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>Top Picks</h2>
        <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(0,224,150,0.12)", border: "1px solid rgba(0,224,150,0.25)", color: "var(--success)" }}>
          High Conviction
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
        {items.map((item) => (
          <StockCard key={`${item.horizon}-${item.symbol}`} analysis={item} compact badge="High Conviction" />
        ))}
      </div>
    </section>
  );
}
