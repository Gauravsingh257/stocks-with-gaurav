"use client";

import { motion } from "framer-motion";
import { ArrowDownRight, ArrowUpRight, Bookmark } from "lucide-react";
import type { Opportunity } from "../_lib/opportunity";
import { priceLabel } from "../_lib/opportunity";

interface Props {
  items: Opportunity[];
  onSelect: (opp: Opportunity) => void;
  onRemove?: (opp: Opportunity) => void;
  emptyHint?: string;
}

const STATUS_COLOR: Record<string, string> = {
  Waiting: "#ffa502",
  Tapped: "#00d4ff",
  Triggered: "#00e096",
};

export default function SmartWatchlistPanel({ items, onSelect, onRemove, emptyHint }: Props) {
  return (
    <div
      style={{
        background: "linear-gradient(160deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015))",
        border: "1px solid var(--border)",
        borderRadius: 16,
        padding: 16,
        backdropFilter: "blur(14px)",
      }}
    >
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1.1 }}>Smart Watchlist</div>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 800, color: "var(--text-primary)" }}>Approaching Zones</h2>
        </div>
        <span
          style={{
            fontSize: "0.62rem",
            color: "var(--text-secondary)",
            background: "rgba(0,212,255,0.1)",
            border: "1px solid var(--accent-dim)",
            padding: "3px 8px",
            borderRadius: 999,
            letterSpacing: 0.5,
            fontWeight: 600,
          }}
        >
          {items.length} active
        </span>
      </header>

      {items.length === 0 ? (
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
          {emptyHint ?? "No setups in your watchlist yet. Add cards from the live feed →"}
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          {items.map((opp, i) => {
            const isBuy = opp.direction === "BUY";
            const DirIcon = isBuy ? ArrowUpRight : ArrowDownRight;
            const dirColor = isBuy ? "#00e096" : "#ff4757";
            const statusColor = STATUS_COLOR[opp.status] ?? "#8899bb";
            return (
              <motion.li
                key={opp.id}
                layout
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 10 }}
                transition={{ duration: 0.22, delay: Math.min(i, 8) * 0.03 }}
              >
                <button
                  type="button"
                  onClick={() => onSelect(opp)}
                  style={{
                    width: "100%",
                    display: "grid",
                    gridTemplateColumns: "minmax(80px, 1.1fr) 0.6fr 1.2fr 0.7fr auto",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 12px",
                    background: "rgba(255,255,255,0.025)",
                    border: "1px solid var(--border)",
                    borderRadius: 10,
                    color: "var(--text-primary)",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 700, fontSize: "0.78rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{opp.symbol}</div>
                    <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", letterSpacing: 0.4, textTransform: "uppercase" }}>Setup {opp.setup}</div>
                  </div>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      padding: "2px 8px",
                      borderRadius: 999,
                      background: `${dirColor}1f`,
                      color: dirColor,
                      border: `1px solid ${dirColor}55`,
                      fontSize: "0.6rem",
                      fontWeight: 700,
                    }}
                  >
                    <DirIcon size={10} /> {isBuy ? "Bull" : "Bear"}
                  </span>
                  <div style={{ fontSize: "0.68rem", color: "var(--text-secondary)", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
                    {priceLabel(opp.stop)} – {priceLabel(opp.entry)}
                  </div>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      fontSize: "0.66rem",
                      fontWeight: 700,
                      color: statusColor,
                    }}
                  >
                    <span aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: statusColor, boxShadow: `0 0 6px ${statusColor}` }} />
                    {opp.status}
                  </span>
                  {onRemove && (
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label={`Remove ${opp.symbol} from watchlist`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemove(opp);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          e.stopPropagation();
                          onRemove(opp);
                        }
                      }}
                      style={{
                        color: "var(--accent)",
                        padding: 4,
                        borderRadius: 6,
                        cursor: "pointer",
                        display: "inline-flex",
                      }}
                    >
                      <Bookmark size={13} fill="currentColor" />
                    </span>
                  )}
                </button>
              </motion.li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
