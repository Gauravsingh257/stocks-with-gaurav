"use client";

/**
 * SystemFlowDiagram — visual explainer for how a stock moves through
 * the platform. Designed to live near the top of /terminal so users
 * understand which panel comes from which stage.
 *
 * Pipeline visualised:
 *   NSE Universe (~2200)  ->  Discovery  ->  Quality  ->  SMC  ->  Final / Watchlist
 *                                                                     ↓
 *                                                                 Portfolio (yours)
 *
 * Plus three sister engines that run in parallel and feed signals
 * outside the main funnel: FVG-Tap, Planned-Execution (G2-6), OI.
 *
 * Real counts wire in via the `funnel` prop (passed from the page's
 * /api/research/discovery payload). When the prop is absent (cold
 * load / off-hours) the diagram renders with placeholder counts.
 *
 * Collapsible — default expanded for first visit, then user choice
 * is remembered in localStorage so the dashboard doesn't lecture
 * every refresh.
 */

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  BarChart3,
  Briefcase,
  ChevronDown,
  ChevronUp,
  Flame,
  Globe2,
  HelpCircle,
  Layers,
  Search,
  ShieldCheck,
  Zap,
} from "lucide-react";

const STORAGE_KEY = "terminal:flow-diagram:expanded:v1";

// ── Types ─────────────────────────────────────────────────────────────
interface FunnelCounts {
  universe_size?: number;
  scanned?: number;
  layer1_pass?: number;
  layer2_pass?: number;
  layer3_pass?: number;
  final_returned?: number;
  watchlist_returned?: number;
  discovery_returned?: number;
}

interface Props {
  funnel?: FunnelCounts | null;
  finalCount?: number;
  watchlistCount?: number;
  portfolioCount?: number; // optional, only if you already have it
}

// ── Stage definitions ────────────────────────────────────────────────
type StageColor = "cyan" | "blue" | "violet" | "emerald" | "gold" | "amber";

interface Stage {
  id: string;
  name: string;
  short: string;
  desc: string;
  detail: string;
  icon: React.ComponentType<{ size?: number }>;
  color: StageColor;
  endpoint?: string;
}

const COLOR_MAP: Record<StageColor, { glow: string; border: string; text: string; bg: string }> = {
  cyan:    { glow: "rgba(34,211,238,0.45)",  border: "rgba(34,211,238,0.55)",  text: "#67e8f9", bg: "rgba(34,211,238,0.10)" },
  blue:    { glow: "rgba(59,130,246,0.45)",  border: "rgba(59,130,246,0.55)",  text: "#93c5fd", bg: "rgba(59,130,246,0.10)" },
  violet:  { glow: "rgba(168,85,247,0.45)",  border: "rgba(168,85,247,0.55)",  text: "#c4b5fd", bg: "rgba(168,85,247,0.10)" },
  emerald: { glow: "rgba(16,185,129,0.55)",  border: "rgba(16,185,129,0.7)",   text: "#34d399", bg: "rgba(16,185,129,0.12)" },
  gold:    { glow: "rgba(245,158,11,0.5)",   border: "rgba(245,158,11,0.6)",   text: "#fbbf24", bg: "rgba(245,158,11,0.10)" },
  amber:   { glow: "rgba(255,165,2,0.45)",   border: "rgba(255,165,2,0.55)",   text: "#ffa502", bg: "rgba(255,165,2,0.10)" },
};

const SIDE_ENGINES = [
  {
    name: "FVG-Tap",
    tag: "Index 5m",
    desc: "NIFTY + BANKNIFTY 5m: CHoCH -> FVG -> tap -> engulf",
    icon: Zap,
    color: "amber" as StageColor,
  },
  {
    name: "Planned Execution",
    tag: "G2-6 daily",
    desc: "Weekly bull + daily order block + daily FVG + confirmation",
    icon: Layers,
    color: "violet" as StageColor,
  },
  {
    name: "OI Intelligence",
    tag: "Options OI",
    desc: "Index options OI buildup + short-covering detection",
    icon: BarChart3,
    color: "blue" as StageColor,
  },
];

// ── Component ────────────────────────────────────────────────────────
export default function SystemFlowDiagram({
  funnel,
  finalCount = 0,
  watchlistCount = 0,
  portfolioCount,
}: Props) {
  const [expanded, setExpanded] = useState<boolean>(true);
  const [hoveredStage, setHoveredStage] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  // Hydrate expanded state from localStorage on first render only.
  // Always default to "expanded" for users who have never seen the diagram.
  useEffect(() => {
    setMounted(true);
    if (typeof window === "undefined") return;
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "false") setExpanded(false);
    } catch {
      /* ignore */
    }
  }, []);

  const toggle = () => {
    setExpanded((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const universe = funnel?.universe_size ?? 2200;
  const scanned = funnel?.scanned ?? funnel?.layer1_pass ?? null;
  const qualityPass = funnel?.layer2_pass ?? null;
  const smcPass = funnel?.layer3_pass ?? null;
  const finalN = funnel?.final_returned ?? finalCount;
  const watchN = funnel?.watchlist_returned ?? watchlistCount;
  const portN = portfolioCount;

  const stages: Stage[] = [
    {
      id: "universe",
      name: "NSE Universe",
      short: "Universe",
      desc: "All listed NSE equities",
      detail: "Every tradable name on the National Stock Exchange. ~2,200 symbols form the starting pool.",
      icon: Globe2,
      color: "cyan",
    },
    {
      id: "discovery",
      name: "Discovery",
      short: "Layer 1",
      desc: "Liquidity + turnover gate",
      detail: "Cuts the universe to names with adequate daily traded value, valid OHLC data, and a positive discovery score. ~800 typically survive.",
      icon: Search,
      color: "blue",
    },
    {
      id: "quality",
      name: "Quality",
      short: "Layer 2",
      desc: "Tech + fundamentals + sentiment",
      detail: "Composite quality scorer: technical strength, fundamental health, news sentiment. Filters out junk before structural analysis. ~200 typically pass.",
      icon: ShieldCheck,
      color: "violet",
    },
    {
      id: "smc",
      name: "SMC",
      short: "Layer 3",
      desc: "CHoCH / OB / FVG / BOS",
      detail: "Smart-Money structural analysis: market-structure shifts, order blocks, fair value gaps, breaks of structure. The final quality gate.",
      icon: Activity,
      color: "emerald",
    },
    {
      id: "final",
      name: "Final Trades",
      short: "Top 6",
      desc: "Highest-conviction setups",
      detail: "Only names that passed ALL three layers. Surfaced as 'Final Trade Ideas' on the Research page and the top of 'Decision-ready setups' on this terminal.",
      icon: Flame,
      color: "gold",
      endpoint: "/api/research/discovery → final_trades",
    },
    {
      id: "portfolio",
      name: "Portfolio",
      short: "Your picks",
      desc: "What you committed to",
      detail: "Persistent positions you've added (manually or via promote). Separate from the live research feed — survives across scans.",
      icon: Briefcase,
      color: "amber",
      endpoint: "/api/portfolio/swing + /longterm",
    },
  ];

  return (
    <section
      style={{
        background: "linear-gradient(160deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015))",
        border: "1px solid var(--border)",
        borderRadius: 16,
        padding: 18,
        marginBottom: 18,
        backdropFilter: "blur(14px)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Subtle animated background gradient */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(circle at 0% 0%, rgba(34,211,238,0.06), transparent 40%), radial-gradient(circle at 100% 100%, rgba(245,158,11,0.06), transparent 40%)",
          pointerEvents: "none",
        }}
      />

      {/* Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          position: "relative",
          marginBottom: expanded ? 16 : 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              padding: 8,
              borderRadius: 10,
              background: "rgba(0,212,255,0.12)",
              border: "1px solid rgba(0,212,255,0.4)",
              display: "grid",
              placeItems: "center",
            }}
          >
            <HelpCircle size={16} color="#00d4ff" />
          </div>
          <div>
            <div
              style={{
                fontSize: "0.62rem",
                color: "var(--text-dim)",
                textTransform: "uppercase",
                letterSpacing: 1.1,
              }}
            >
              How this works
            </div>
            <h2
              style={{
                margin: 0,
                fontSize: "1.05rem",
                fontWeight: 800,
                color: "var(--text-primary)",
              }}
            >
              Stock journey: Universe → Final → Portfolio
            </h2>
          </div>
        </div>
        <button
          onClick={toggle}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            background: "rgba(255,255,255,0.04)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            color: "var(--text-secondary)",
            fontSize: "0.72rem",
            fontWeight: 600,
            cursor: "pointer",
          }}
          aria-label={expanded ? "Collapse explainer" : "Expand explainer"}
        >
          {expanded ? "Hide" : "Show"}
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </header>

      <AnimatePresence initial={false}>
        {expanded && mounted && (
          <motion.div
            key="content"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: "easeOut" }}
            style={{ overflow: "hidden", position: "relative" }}
          >
            {/* ── Main funnel ─────────────────────────────────────── */}
            <div
              style={{
                perspective: "1400px",
                padding: "18px 4px 8px",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))`,
                  gap: 8,
                  alignItems: "stretch",
                  position: "relative",
                }}
              >
                {/* Animated flow lines (behind cards) */}
                <svg
                  aria-hidden
                  viewBox="0 0 1200 100"
                  preserveAspectRatio="none"
                  style={{
                    position: "absolute",
                    top: "50%",
                    left: 0,
                    width: "100%",
                    height: "60%",
                    transform: "translateY(-50%)",
                    pointerEvents: "none",
                    zIndex: 0,
                  }}
                >
                  <defs>
                    <linearGradient id="flowGrad" x1="0" y1="0" x2="1" y2="0">
                      <stop offset="0%" stopColor="rgba(34,211,238,0.0)" />
                      <stop offset="20%" stopColor="rgba(34,211,238,0.5)" />
                      <stop offset="60%" stopColor="rgba(16,185,129,0.6)" />
                      <stop offset="100%" stopColor="rgba(245,158,11,0.0)" />
                    </linearGradient>
                  </defs>
                  <line
                    x1="0"
                    y1="50"
                    x2="1200"
                    y2="50"
                    stroke="url(#flowGrad)"
                    strokeWidth="2"
                  />
                  {/* Flowing particles. cx is set initially to 0 so the
                      circle is valid on first paint before framer-motion's
                      animate loop starts overriding it. */}
                  {[0, 0.33, 0.66].map((delay, i) => (
                    <motion.circle
                      key={`particle-${i}`}
                      r="3"
                      fill="#22d3ee"
                      cx={0}
                      cy={50}
                      initial={{ cx: 0 }}
                      animate={{ cx: 1200 }}
                      transition={{
                        duration: 4,
                        repeat: Infinity,
                        ease: "linear",
                        delay: delay * 4,
                      }}
                      style={{ filter: "drop-shadow(0 0 6px rgba(34,211,238,0.8))" }}
                    />
                  ))}
                  {[0.15, 0.5, 0.85].map((delay, i) => (
                    <motion.circle
                      key={`particle2-${i}`}
                      r="2"
                      fill="#10b981"
                      cx={0}
                      cy={50}
                      initial={{ cx: 0 }}
                      animate={{ cx: 1200 }}
                      transition={{
                        duration: 5,
                        repeat: Infinity,
                        ease: "linear",
                        delay: delay * 5,
                      }}
                      style={{ filter: "drop-shadow(0 0 4px rgba(16,185,129,0.8))" }}
                    />
                  ))}
                </svg>

                {stages.map((stage, idx) => {
                  const c = COLOR_MAP[stage.color];
                  const Icon = stage.icon;
                  const count =
                    stage.id === "universe" ? universe :
                    stage.id === "discovery" ? scanned :
                    stage.id === "quality" ? qualityPass :
                    stage.id === "smc" ? smcPass :
                    stage.id === "final" ? finalN :
                    stage.id === "portfolio" ? portN :
                    null;
                  const isHovered = hoveredStage === stage.id;

                  return (
                    <motion.div
                      key={stage.id}
                      onHoverStart={() => setHoveredStage(stage.id)}
                      onHoverEnd={() => setHoveredStage(null)}
                      whileHover={{
                        translateY: -6,
                        rotateY: 0,
                        scale: 1.04,
                      }}
                      transition={{ type: "spring", stiffness: 320, damping: 22 }}
                      style={{
                        position: "relative",
                        zIndex: isHovered ? 10 : 1,
                        transformStyle: "preserve-3d",
                        transform: `rotateY(${(idx - (stages.length - 1) / 2) * 1.5}deg)`,
                      }}
                    >
                      <div
                        style={{
                          background: `linear-gradient(160deg, ${c.bg}, rgba(0,0,0,0.25))`,
                          border: `1px solid ${c.border}`,
                          borderRadius: 14,
                          padding: 12,
                          minHeight: 140,
                          display: "flex",
                          flexDirection: "column",
                          gap: 6,
                          boxShadow: isHovered
                            ? `0 18px 48px ${c.glow}, 0 0 26px ${c.glow}, inset 0 1px 0 rgba(255,255,255,0.08)`
                            : `0 8px 24px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04)`,
                          transition: "box-shadow 0.2s ease",
                          cursor: "default",
                          position: "relative",
                          overflow: "hidden",
                        }}
                      >
                        {/* Top: icon + short label */}
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div
                            style={{
                              padding: 6,
                              borderRadius: 8,
                              background: c.bg,
                              border: `1px solid ${c.border}`,
                              display: "grid",
                              placeItems: "center",
                            }}
                          >
                            <Icon size={14} />
                          </div>
                          <span
                            style={{
                              fontSize: "0.58rem",
                              color: c.text,
                              textTransform: "uppercase",
                              fontWeight: 800,
                              letterSpacing: 1,
                            }}
                          >
                            {stage.short}
                          </span>
                        </div>

                        {/* Name */}
                        <div
                          style={{
                            fontSize: "0.86rem",
                            fontWeight: 800,
                            color: "var(--text-primary)",
                            lineHeight: 1.2,
                          }}
                        >
                          {stage.name}
                        </div>

                        {/* Count (the live data point) */}
                        <div
                          style={{
                            fontSize: count !== null && count !== undefined ? "1.4rem" : "1rem",
                            fontWeight: 900,
                            color: c.text,
                            lineHeight: 1,
                            marginTop: 2,
                            textShadow: `0 0 12px ${c.glow}`,
                          }}
                        >
                          {count !== null && count !== undefined
                            ? typeof count === "number" && count > 999
                              ? count.toLocaleString()
                              : count
                            : "—"}
                        </div>

                        {/* Description */}
                        <div
                          style={{
                            fontSize: "0.66rem",
                            color: "var(--text-dim)",
                            lineHeight: 1.35,
                            marginTop: 2,
                          }}
                        >
                          {stage.desc}
                        </div>

                        {/* Hover detail (absolute overlay) */}
                        <AnimatePresence>
                          {isHovered && (
                            <motion.div
                              initial={{ opacity: 0, y: 6 }}
                              animate={{ opacity: 1, y: 0 }}
                              exit={{ opacity: 0, y: 6 }}
                              transition={{ duration: 0.18 }}
                              style={{
                                position: "absolute",
                                top: "calc(100% + 8px)",
                                left: "50%",
                                transform: "translateX(-50%)",
                                width: 260,
                                padding: 12,
                                background: "rgba(11,15,28,0.96)",
                                border: `1px solid ${c.border}`,
                                borderRadius: 10,
                                boxShadow: `0 16px 40px ${c.glow}`,
                                zIndex: 20,
                                fontSize: "0.7rem",
                                color: "var(--text-secondary)",
                                lineHeight: 1.5,
                                pointerEvents: "none",
                              }}
                            >
                              <div style={{ color: c.text, fontWeight: 800, marginBottom: 6, fontSize: "0.74rem" }}>
                                {stage.name}
                              </div>
                              {stage.detail}
                              {stage.endpoint && (
                                <div
                                  style={{
                                    marginTop: 8,
                                    paddingTop: 8,
                                    borderTop: "1px solid rgba(255,255,255,0.08)",
                                    fontSize: "0.6rem",
                                    color: "var(--text-dim)",
                                    fontFamily: "ui-monospace, monospace",
                                  }}
                                >
                                  {stage.endpoint}
                                </div>
                              )}
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>
                    </motion.div>
                  );
                })}
              </div>
            </div>

            {/* ── Sister engines (parallel, not part of main funnel) ── */}
            <div
              style={{
                marginTop: 28,
                padding: "12px 14px",
                background: "rgba(0,0,0,0.18)",
                border: "1px dashed rgba(255,255,255,0.1)",
                borderRadius: 12,
              }}
            >
              <div
                style={{
                  fontSize: "0.62rem",
                  color: "var(--text-dim)",
                  textTransform: "uppercase",
                  letterSpacing: 1.1,
                  marginBottom: 10,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <span
                  style={{
                    height: 1,
                    flex: 1,
                    background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent)",
                  }}
                />
                <span>Parallel engines · not part of the funnel above</span>
                <span
                  style={{
                    height: 1,
                    flex: 1,
                    background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent)",
                  }}
                />
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: `repeat(auto-fit, minmax(220px, 1fr))`,
                  gap: 10,
                }}
              >
                {SIDE_ENGINES.map((eng) => {
                  const c = COLOR_MAP[eng.color];
                  const Icon = eng.icon;
                  return (
                    <motion.div
                      key={eng.name}
                      whileHover={{ translateY: -3 }}
                      transition={{ type: "spring", stiffness: 300, damping: 20 }}
                      style={{
                        background: `linear-gradient(140deg, ${c.bg}, rgba(0,0,0,0.2))`,
                        border: `1px solid ${c.border}`,
                        borderRadius: 10,
                        padding: 10,
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 10,
                      }}
                    >
                      <div
                        style={{
                          padding: 7,
                          borderRadius: 8,
                          background: c.bg,
                          border: `1px solid ${c.border}`,
                          display: "grid",
                          placeItems: "center",
                          flexShrink: 0,
                        }}
                      >
                        <Icon size={14} />
                      </div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            marginBottom: 2,
                          }}
                        >
                          <span style={{ fontSize: "0.8rem", fontWeight: 800, color: "var(--text-primary)" }}>
                            {eng.name}
                          </span>
                          <span
                            style={{
                              fontSize: "0.56rem",
                              color: c.text,
                              padding: "1px 6px",
                              border: `1px solid ${c.border}`,
                              borderRadius: 4,
                              textTransform: "uppercase",
                              fontWeight: 700,
                              letterSpacing: 0.6,
                            }}
                          >
                            {eng.tag}
                          </span>
                        </div>
                        <div style={{ fontSize: "0.65rem", color: "var(--text-dim)", lineHeight: 1.4 }}>
                          {eng.desc}
                        </div>
                      </div>
                    </motion.div>
                  );
                })}
              </div>
            </div>

            {/* ── Quick reading guide ─────────────────────────────── */}
            <div
              style={{
                marginTop: 14,
                padding: "10px 12px",
                background: "rgba(0,212,255,0.04)",
                border: "1px solid rgba(0,212,255,0.18)",
                borderRadius: 10,
                fontSize: "0.7rem",
                color: "var(--text-secondary)",
                lineHeight: 1.55,
              }}
            >
              <span style={{ color: "#00d4ff", fontWeight: 800 }}>Reading guide: </span>
              The five-stage funnel feeds <strong style={{ color: "var(--text-primary)" }}>Decision-ready setups</strong> (full feed) and
              <strong style={{ color: "var(--text-primary)" }}> Final Trade Ideas</strong> (top 6).
              <strong style={{ color: "var(--text-primary)" }}> Approaching Zones</strong> shows the watchlist subset.
              The three parallel engines below run separately and surface their own signals — they are NOT inputs to the funnel.
              Your <strong style={{ color: "var(--text-primary)" }}>Portfolio</strong> is whatever you've personally committed to track.
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
