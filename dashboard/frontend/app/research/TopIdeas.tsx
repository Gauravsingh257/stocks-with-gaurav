"use client";

import StockCard from "@/components/StockCard";
import type { LongTermIdea, StockAnalysis, SwingIdea } from "@/lib/api";
import { recommendationLabelFromScanResult } from "@/utils/calculateConfidence";

function swingToAnalysis(item: SwingIdea): StockAnalysis {
  const row: StockAnalysis = {
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
    recommendation: "Avoid",
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
  return { ...row, recommendation: recommendationLabelFromScanResult(row) };
}

function longTermToAnalysis(item: LongTermIdea): StockAnalysis {
  const entryZone = Array.isArray(item.entry_zone) && item.entry_zone.length >= 2
    ? [item.entry_zone[0], item.entry_zone[1]]
    : [Number((item.entry_price * 0.98).toFixed(2)), item.entry_price];
  const row: StockAnalysis = {
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
    recommendation: "Avoid",
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
  return { ...row, recommendation: recommendationLabelFromScanResult(row) };
}

function sortIdeas(items: StockAnalysis[]): StockAnalysis[] {
  return [...items].sort((a, b) => {
    const conf = b.confidence_score - a.confidence_score;
    if (conf !== 0) return conf;
    return (b.risk_reward || 0) - (a.risk_reward || 0);
  });
}

function IdeaSubsection({
  title,
  subtitle,
  items,
  emptyHint,
}: {
  title: string;
  subtitle: string;
  items: StockAnalysis[];
  emptyHint: string;
}) {
  if (items.length === 0) {
    return (
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <h3 className="m-0 text-base font-bold" style={{ color: "var(--text-primary)" }}>{title}</h3>
          <span style={{ fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4, background: "rgba(148,163,184,0.1)", border: "1px solid var(--border)", color: "var(--text-dim)" }}>
            —
          </span>
        </div>
        <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: "0.8rem" }}>{emptyHint}</p>
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h3 className="m-0 text-base font-bold" style={{ color: "var(--text-primary)" }}>{title}</h3>
          <span style={{ fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4, background: "rgba(0,224,150,0.1)", border: "1px solid rgba(0,224,150,0.22)", color: "var(--success)" }}>
            Top {items.length}
          </span>
        </div>
        <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>{subtitle}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
        {items.map((item) => (
          <StockCard key={`${title}-${item.horizon}-${item.symbol}`} analysis={item} compact badge="High Conviction" />
        ))}
      </div>
    </div>
  );
}

export function TopIdeas({ swing, longterm }: { swing: SwingIdea[]; longterm: LongTermIdea[] }) {
  const topSwing = sortIdeas(swing.map(swingToAnalysis)).slice(0, 3);
  const topLt = sortIdeas(longterm.map(longTermToAnalysis)).slice(0, 3);
  const any = topSwing.length > 0 || topLt.length > 0;

  if (!any) {
    return (
      <section className="glass" style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <div style={{ width: 4, height: 24, borderRadius: 2, background: "var(--success)" }} />
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>High Conviction Picks</h2>
          <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(148,163,184,0.12)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            Awaiting Setups
          </span>
        </div>
        <p style={{ margin: "0 0 12px", color: "var(--text-secondary)", fontSize: "0.84rem", lineHeight: 1.55 }}>
          No setups found in current market conditions. Use <strong>Global NSE Search</strong> for on-demand analysis, or run a scan to refresh ranked lists.
        </p>
      </section>
    );
  }

  return (
    <section>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 4, height: 24, borderRadius: 2, background: "var(--success)" }} />
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>High Conviction Picks</h2>
          <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(0,224,150,0.12)", border: "1px solid rgba(0,224,150,0.25)", color: "var(--success)" }}>
            Top 3 per horizon
          </span>
        </div>
        <div style={{ color: "var(--text-secondary)", fontSize: "0.76rem" }}>
          Sorted by confidence, then risk/reward
        </div>
      </div>

      <IdeaSubsection
        title="Swing (1–8 weeks)"
        subtitle="Top 3 from latest swing scan"
        items={topSwing}
        emptyHint="No swing setups currently — run a swing scan or use global search."
      />
      <IdeaSubsection
        title="Long-term (6–24 months)"
        subtitle="Top 3 from latest long-term scan"
        items={topLt}
        emptyHint="No long-term setups currently — run a long-term scan or check swing tab."
      />
    </section>
  );
}
