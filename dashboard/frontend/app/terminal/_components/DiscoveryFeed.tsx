"use client";

import { motion } from "framer-motion";
import { Activity, Eye, Sparkles, Waves } from "lucide-react";
import type { Opportunity } from "../_lib/opportunity";

interface Props {
  items: Opportunity[];
  onSelect: (opp: Opportunity) => void;
}

interface FeedItem {
  id: string;
  kind: "new" | "sweep" | "approaching";
  opp: Opportunity;
}

function classify(opp: Opportunity): FeedItem["kind"] {
  if (opp.status === "Triggered") return "new";
  if (opp.scores.liquidity && opp.scores.structure) return "sweep";
  return "approaching";
}

const META: Record<FeedItem["kind"], { title: string; subtitle: string; color: string; icon: React.ReactNode }> = {
  new: {
    title: "New Setup Detected",
    subtitle: "All SMC layers passed — fresh trade idea on the tape.",
    color: "#00e096",
    icon: <Sparkles size={14} />,
  },
  sweep: {
    title: "Liquidity Sweep Confirmed",
    subtitle: "Price swept resting liquidity and shifted structure.",
    color: "#00d4ff",
    icon: <Waves size={14} />,
  },
  approaching: {
    title: "Entry Zone Approaching",
    subtitle: "CMP is gravitating toward the order block.",
    color: "#ffa502",
    icon: <Activity size={14} />,
  },
};

export default function DiscoveryFeed({ items, onSelect }: Props) {
  const feed: FeedItem[] = items.slice(0, 12).map((opp) => ({
    id: opp.id,
    kind: classify(opp),
    opp,
  }));

  return (
    <div>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1.1 }}>Live Feed</div>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 800, color: "var(--text-primary)" }}>Discovery Stream</h2>
        </div>
        <span style={{ fontSize: "0.66rem", color: "var(--text-dim)" }}>Auto-curated · realtime</span>
      </header>

      {feed.length === 0 ? (
        <div
          style={{
            padding: 18,
            background: "rgba(255,255,255,0.02)",
            border: "1px dashed var(--border)",
            borderRadius: 12,
            textAlign: "center",
            fontSize: "0.74rem",
            color: "var(--text-dim)",
          }}
        >
          No fresh signals yet — engine is scanning.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {feed.map((item, i) => {
            const meta = META[item.kind];
            const isBuy = item.opp.direction === "BUY";
            return (
              <motion.button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.opp)}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, delay: Math.min(i, 8) * 0.04 }}
                whileHover={{ x: 2 }}
                style={{
                  textAlign: "left",
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto",
                  gap: 12,
                  alignItems: "center",
                  padding: 12,
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid var(--border)",
                  borderRadius: 12,
                  color: "var(--text-primary)",
                  cursor: "pointer",
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                <span
                  aria-hidden
                  style={{
                    position: "absolute",
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 3,
                    background: meta.color,
                    boxShadow: `0 0 12px ${meta.color}`,
                  }}
                />
                <div
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: 10,
                    background: `${meta.color}1f`,
                    border: `1px solid ${meta.color}55`,
                    color: meta.color,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {meta.icon}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                    <span style={{ fontWeight: 700, fontSize: "0.78rem" }}>{meta.title}</span>
                    <span style={{ fontSize: "0.66rem", color: "var(--text-dim)" }}>·</span>
                    <span style={{ fontSize: "0.7rem", color: "var(--text-secondary)", fontWeight: 600 }}>{item.opp.symbol}</span>
                    <span
                      style={{
                        fontSize: "0.58rem",
                        padding: "1px 6px",
                        borderRadius: 4,
                        fontWeight: 700,
                        background: isBuy ? "rgba(0,224,150,0.15)" : "rgba(255,71,87,0.15)",
                        color: isBuy ? "#00e096" : "#ff4757",
                        letterSpacing: 0.5,
                      }}
                    >
                      {item.opp.direction}
                    </span>
                  </div>
                  <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", marginTop: 2 }}>{meta.subtitle}</div>
                </div>
                <Eye size={14} color="var(--text-dim)" />
              </motion.button>
            );
          })}
        </div>
      )}
    </div>
  );
}
