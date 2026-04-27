"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";

/**
 * TradeReplay — "Trading Netflix" step-by-step trade replay.
 *
 * User sees the trade unfold one node at a time:
 *   Step 1 → Market context
 *   Step 2 → Liquidity sweep
 *   Step 3 → OB formed
 *   Step 4 → Entry
 *   Step 5 → Outcome
 *
 * Features:
 *   - Time-based slider to scrub through steps
 *   - Auto-play mode with configurable speed
 *   - Nodes appear with fade-in animation
 *   - Emotion-synced background colors
 *   - Narrative text below each step
 *
 * Fetches from: GET /api/trades/{graphId}/graph/website
 */

interface GraphNode {
  id: string;
  type: string;
  label: string;
  emotion: string;
  data: Record<string, unknown>;
  position: { x: number; y: number };
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  type: string;
  animated: boolean;
}

interface TradeGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface NarrativeStep {
  step: number;
  kind: string;
  label: string;
  emotion: string;
  narrative: string;
  visual: string;
  data: Record<string, unknown>;
}

// Emotion → gradient background
const EMOTION_BG: Record<string, string> = {
  tension:      "from-red-950/40 to-zinc-900",
  curiosity:    "from-amber-950/40 to-zinc-900",
  fear:         "from-red-950/50 to-zinc-900",
  surprise:     "from-yellow-950/40 to-zinc-900",
  insight:      "from-blue-950/40 to-zinc-900",
  confidence:   "from-green-950/40 to-zinc-900",
  triumph:      "from-emerald-950/40 to-zinc-900",
  satisfaction: "from-emerald-950/30 to-zinc-900",
  lesson:       "from-purple-950/40 to-zinc-900",
  risk:         "from-red-950/30 to-zinc-900",
  decision:     "from-indigo-950/40 to-zinc-900",
  precision:    "from-cyan-950/30 to-zinc-900",
  context:      "from-slate-900/60 to-zinc-900",
  control:      "from-blue-950/30 to-zinc-900",
  shift:        "from-orange-950/30 to-zinc-900",
  neutral:      "from-zinc-800/40 to-zinc-900",
  ambition:     "from-teal-950/30 to-zinc-900",
  confirmation: "from-indigo-950/30 to-zinc-900",
};

const NODE_ICONS: Record<string, string> = {
  REGIME:           "🏛️",
  HTF_BIAS:         "📐",
  LIQUIDITY_SWEEP:  "💧",
  DISPLACEMENT:     "⚡",
  CHOCH:            "🔄",
  BOS:              "📊",
  ORDER_BLOCK:      "🧱",
  FVG:              "📭",
  CONFLUENCE:       "🎯",
  OI_SIGNAL:        "📊",
  VOLUME:           "📈",
  ENTRY:            "🚀",
  STOP_LOSS:        "🛑",
  TARGET:           "🎯",
  TRAIL_MOVE:       "📏",
  OUTCOME:          "✅",
};

const STEP_LABELS: Record<string, string> = {
  REGIME:           "Market Context",
  HTF_BIAS:         "Higher Timeframe",
  LIQUIDITY_SWEEP:  "Liquidity Trap",
  DISPLACEMENT:     "Smart Money Move",
  CHOCH:            "Trend Reversal",
  BOS:              "Structure Break",
  ORDER_BLOCK:      "Institutional Zone",
  FVG:              "Imbalance Zone",
  CONFLUENCE:       "Setup Score",
  OI_SIGNAL:        "Options Data",
  VOLUME:           "Volume Signal",
  ENTRY:            "Trade Entry",
  STOP_LOSS:        "Risk Defined",
  TARGET:           "Profit Target",
  TRAIL_MOVE:       "Stop Adjusted",
  OUTCOME:          "Result",
};

// Narratives that are engaging (not robotic)
function getNarrative(node: GraphNode): string {
  const d = node.data || {};
  const kind = node.type;

  const narrativeMap: Record<string, () => string> = {
    REGIME:           () => `The market was in a ${String(d.regime || "unknown").toLowerCase()} phase. This set the stage for what came next.`,
    HTF_BIAS:         () => `Higher timeframes showed ${String(d.bias || "no clear").toLowerCase()} structure — the bigger picture was clear.`,
    LIQUIDITY_SWEEP:  () => `Stops got hunted. Retail traders panicked and sold. This was the trap.`,
    DISPLACEMENT:     () => `A massive, decisive candle appeared. Smart money was making their move.`,
    CHOCH:            () => `Change of Character at ${d.level || "key level"}. The old trend was dead.`,
    BOS:              () => `Break of Structure confirmed. The new trend direction was locked in.`,
    ORDER_BLOCK:      () => `Institutional demand zone between ${d.low || "?"} and ${d.high || "?"}. This is where smart money placed orders.`,
    FVG:              () => `Fair Value Gap — an imbalance that price wants to fill. Confirmation stacked.`,
    CONFLUENCE:       () => `Everything aligned. Confluence score: ${d.total || "?"}/10. ${Number(d.total) >= 7 ? "A+ setup." : "Solid setup."}`,
    OI_SIGNAL:        () => `Open Interest data confirmed the direction. Options flow aligned.`,
    VOLUME:           () => `Volume expanded above average — confirming institutional participation.`,
    ENTRY:            () => `Entry triggered: ${d.type || "?"} at ${d.price || "?"}. Trade is live.`,
    STOP_LOSS:        () => `Stop loss set at ${d.price || "?"}. Risk is defined. ${d.original ? "Original level." : "Adjusted."}`,
    TARGET:           () => `Target set at ${d.price || "?"} for ${d.rr || "?"}R reward. The math works.`,
    TRAIL_MOVE:       () => `Trailing stop moved to ${d.new_sl || "?"} — locking in profits at Stage ${d.stage || "?"}.`,
    OUTCOME:          () => {
      const result = String(d.result || "?");
      const exitR = Number(d.exit_r || 0);
      if (result === "WIN") return `Target hit! +${exitR.toFixed(1)}R profit captured. The setup played out perfectly.`;
      if (result === "LOSS") return `Stop loss hit. ${exitR.toFixed(1)}R loss. Time to analyze and learn.`;
      return `Trailed exit. ${exitR >= 0 ? "+" : ""}${exitR.toFixed(1)}R captured via trailing stop.`;
    },
  };

  return (narrativeMap[kind] || (() => node.label))();
}

export default function TradeReplay({
  graphId,
  apiBase = "",
}: {
  graphId: string;
  apiBase?: string;
}) {
  const [data, setData] = useState<TradeGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Replay state
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(2000); // ms per step
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch graph
  useEffect(() => {
    if (!graphId) return;
    setLoading(true);
    setError(null);

    fetch(`${apiBase}/api/trades/${encodeURIComponent(graphId)}/graph/website`, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => {
        setData(d);
        setCurrentStep(0);
        setIsPlaying(false);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [graphId, apiBase]);

  // Auto-play
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);

    if (isPlaying && data) {
      timerRef.current = setInterval(() => {
        setCurrentStep((prev) => {
          if (prev >= data.nodes.length - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, speed);
    }

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isPlaying, speed, data]);

  const togglePlay = useCallback(() => {
    if (!data) return;
    if (currentStep >= data.nodes.length - 1) {
      setCurrentStep(0);
      setIsPlaying(true);
    } else {
      setIsPlaying((p) => !p);
    }
  }, [currentStep, data]);

  const handleSlider = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setIsPlaying(false);
      setCurrentStep(Number(e.target.value));
    },
    [],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400 text-sm">
        Loading trade replay…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400 text-sm">
        {error}
      </div>
    );
  }
  if (!data || !data.nodes.length) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-500 text-sm">
        No replay data
      </div>
    );
  }

  const totalSteps = data.nodes.length;
  const visibleNodes = data.nodes.slice(0, currentStep + 1);
  const activeNode = data.nodes[currentStep];
  const emotion = activeNode?.emotion || "neutral";
  const bgGradient = EMOTION_BG[emotion] || EMOTION_BG.neutral;

  return (
    <div
      className={`rounded-xl border border-zinc-700/50 bg-gradient-to-b ${bgGradient} p-5 transition-all duration-700`}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-zinc-300 flex items-center gap-2">
          <span className="text-amber-400">▶</span> Trade Replay
        </h3>
        <span className="text-xs text-zinc-500">
          Step {currentStep + 1} / {totalSteps}
        </span>
      </div>

      {/* Main display — current step */}
      <div
        className="relative mb-5 p-6 bg-zinc-900/60 rounded-lg border border-zinc-700/40 min-h-[180px] flex flex-col items-center justify-center transition-all duration-500"
        key={activeNode?.id}
      >
        {/* Icon */}
        <div className="text-4xl mb-3 animate-[fadeIn_0.5s_ease-in]">
          {NODE_ICONS[activeNode?.type] || "⚙️"}
        </div>

        {/* Step label */}
        <div className="text-xs text-zinc-500 uppercase tracking-wider mb-1">
          {STEP_LABELS[activeNode?.type] || activeNode?.type}
        </div>

        {/* Main label */}
        <div className="text-lg font-semibold text-zinc-100 text-center mb-3">
          {activeNode?.label}
        </div>

        {/* Narrative */}
        <p className="text-sm text-zinc-400 text-center max-w-md leading-relaxed">
          {getNarrative(activeNode)}
        </p>

        {/* Data points (if interesting) */}
        {activeNode?.data && Object.keys(activeNode.data).length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 justify-center">
            {Object.entries(activeNode.data)
              .filter(([k]) => !["original"].includes(k))
              .slice(0, 4)
              .map(([k, v]) => (
                <span
                  key={k}
                  className="px-2 py-0.5 bg-zinc-800/80 rounded text-xs text-zinc-400"
                >
                  {k}: {String(v)}
                </span>
              ))}
          </div>
        )}
      </div>

      {/* Timeline — mini nodes showing progress */}
      <div className="flex items-center gap-1 mb-4 overflow-x-auto pb-1">
        {data.nodes.map((node, i) => {
          const isVisible = i <= currentStep;
          const isActive = i === currentStep;
          return (
            <button
              key={node.id}
              onClick={() => {
                setIsPlaying(false);
                setCurrentStep(i);
              }}
              className={`
                flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-xs
                transition-all duration-300 border
                ${isActive
                  ? "border-amber-500 bg-amber-500/20 scale-110"
                  : isVisible
                    ? "border-zinc-600 bg-zinc-800/60 opacity-80"
                    : "border-zinc-800 bg-zinc-900/40 opacity-30"
                }
              `}
              title={`${STEP_LABELS[node.type] || node.type}: ${node.label}`}
            >
              {NODE_ICONS[node.type]?.slice(0, 2) || "?"}
            </button>
          );
        })}
      </div>

      {/* Slider */}
      <input
        type="range"
        min={0}
        max={totalSteps - 1}
        value={currentStep}
        onChange={handleSlider}
        className="w-full h-1 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-amber-500 mb-4"
      />

      {/* Controls */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {/* Play/Pause */}
          <button
            onClick={togglePlay}
            className="px-3 py-1.5 rounded-lg bg-amber-600/20 border border-amber-600/40 text-amber-400 text-sm hover:bg-amber-600/30 transition-colors"
          >
            {isPlaying ? "⏸ Pause" : currentStep >= totalSteps - 1 ? "🔄 Replay" : "▶ Play"}
          </button>

          {/* Speed */}
          <select
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            className="px-2 py-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-400 text-xs"
          >
            <option value={3000}>0.5x</option>
            <option value={2000}>1x</option>
            <option value={1000}>2x</option>
            <option value={500}>4x</option>
          </select>
        </div>

        {/* Step nav */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => { setIsPlaying(false); setCurrentStep(Math.max(0, currentStep - 1)); }}
            disabled={currentStep === 0}
            className="px-2 py-1 rounded bg-zinc-800 text-zinc-400 text-xs disabled:opacity-30 hover:bg-zinc-700 transition-colors"
          >
            ◀ Prev
          </button>
          <button
            onClick={() => { setIsPlaying(false); setCurrentStep(Math.min(totalSteps - 1, currentStep + 1)); }}
            disabled={currentStep >= totalSteps - 1}
            className="px-2 py-1 rounded bg-zinc-800 text-zinc-400 text-xs disabled:opacity-30 hover:bg-zinc-700 transition-colors"
          >
            Next ▶
          </button>
        </div>
      </div>
    </div>
  );
}
