"use client";

import React, { useEffect, useRef, useState } from "react";

/**
 * TradeGraph — Interactive "Why This Trade?" visualization.
 *
 * Renders the causal chain of a trade as a vertical DAG:
 *   Regime → HTF Bias → Liquidity → CHoCH → BOS → OB → FVG → Entry → Outcome
 *
 * Uses pure SVG (no D3 dependency) for lightweight rendering.
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

// Node styling by kind
const NODE_STYLES: Record<string, { bg: string; border: string; icon: string }> = {
  REGIME:           { bg: "#1a1a2e", border: "#6366f1", icon: "🏛️" },
  HTF_BIAS:         { bg: "#1a1a2e", border: "#8b5cf6", icon: "📐" },
  LIQUIDITY_SWEEP:  { bg: "#1c1917", border: "#f59e0b", icon: "💧" },
  DISPLACEMENT:     { bg: "#1c1917", border: "#f97316", icon: "⚡" },
  CHOCH:            { bg: "#172554", border: "#3b82f6", icon: "🔄" },
  BOS:              { bg: "#172554", border: "#06b6d4", icon: "📊" },
  ORDER_BLOCK:      { bg: "#14532d", border: "#22c55e", icon: "🧱" },
  FVG:              { bg: "#14532d", border: "#10b981", icon: "📭" },
  CONFLUENCE:       { bg: "#4c1d95", border: "#a78bfa", icon: "🎯" },
  OI_SIGNAL:        { bg: "#1e1b4b", border: "#818cf8", icon: "📊" },
  VOLUME:           { bg: "#1e1b4b", border: "#818cf8", icon: "📈" },
  ENTRY:            { bg: "#064e3b", border: "#34d399", icon: "🚀" },
  STOP_LOSS:        { bg: "#450a0a", border: "#ef4444", icon: "🛑" },
  TARGET:           { bg: "#064e3b", border: "#22c55e", icon: "🎯" },
  TRAIL_MOVE:       { bg: "#1e3a5f", border: "#60a5fa", icon: "📏" },
  OUTCOME:          { bg: "#1a1a2e", border: "#fbbf24", icon: "✅" },
};

const DEFAULT_STYLE = { bg: "#1a1a2e", border: "#6b7280", icon: "⚙️" };

const NODE_WIDTH = 260;
const NODE_HEIGHT = 56;
const VERTICAL_GAP = 24;

export default function TradeGraph({
  graphId,
  apiBase = "",
}: {
  graphId: string;
  apiBase?: string;
}) {
  const [data, setData] = useState<TradeGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!graphId) return;
    setLoading(true);
    setError(null);

    fetch(`${apiBase}/api/trades/${encodeURIComponent(graphId)}/graph/website`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => setData(d))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [graphId, apiBase]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400 text-sm">
        Loading trade graph…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400 text-sm">
        Failed to load graph: {error}
      </div>
    );
  }
  if (!data || !data.nodes.length) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-500 text-sm">
        No graph data available
      </div>
    );
  }

  // Layout: vertical flow, centered horizontally
  const centerX = NODE_WIDTH / 2 + 40;
  const nodes = data.nodes.map((n, i) => ({
    ...n,
    cx: centerX,
    cy: 40 + i * (NODE_HEIGHT + VERTICAL_GAP),
  }));

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const svgHeight = 40 + nodes.length * (NODE_HEIGHT + VERTICAL_GAP) + 20;
  const svgWidth = NODE_WIDTH + 80;

  return (
    <div className="rounded-xl border border-zinc-700/50 bg-zinc-900/80 p-4 overflow-auto">
      <h3 className="text-sm font-semibold text-zinc-300 mb-3 flex items-center gap-2">
        <span className="text-indigo-400">⬡</span> Why This Trade?
      </h3>
      <svg
        ref={svgRef}
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="mx-auto"
      >
        {/* Edges */}
        {data.edges.map((edge) => {
          const src = nodeMap.get(edge.source);
          const tgt = nodeMap.get(edge.target);
          if (!src || !tgt) return null;

          const x1 = src.cx + NODE_WIDTH / 2;
          const y1 = src.cy + NODE_HEIGHT;
          const x2 = tgt.cx + NODE_WIDTH / 2;
          const y2 = tgt.cy;

          return (
            <g key={edge.id}>
              <line
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="#4b5563"
                strokeWidth={1.5}
                strokeDasharray={edge.animated ? "6 3" : "none"}
                opacity={0.6}
              />
              {/* Edge label */}
              {edge.label && (
                <text
                  x={(x1 + x2) / 2 + 8}
                  y={(y1 + y2) / 2}
                  fill="#9ca3af"
                  fontSize={9}
                  textAnchor="start"
                >
                  {edge.label}
                </text>
              )}
              {/* Arrow */}
              <polygon
                points={`${x2},${y2} ${x2 - 4},${y2 - 8} ${x2 + 4},${y2 - 8}`}
                fill="#4b5563"
              />
            </g>
          );
        })}

        {/* Nodes */}
        {nodes.map((node) => {
          const style = NODE_STYLES[node.type] || DEFAULT_STYLE;
          const isHovered = hoveredNode === node.id;

          return (
            <g
              key={node.id}
              onMouseEnter={() => setHoveredNode(node.id)}
              onMouseLeave={() => setHoveredNode(null)}
              style={{ cursor: "pointer" }}
            >
              <rect
                x={node.cx}
                y={node.cy}
                width={NODE_WIDTH}
                height={NODE_HEIGHT}
                rx={10}
                fill={style.bg}
                stroke={style.border}
                strokeWidth={isHovered ? 2.5 : 1.5}
                opacity={isHovered ? 1 : 0.85}
              />
              {/* Icon */}
              <text
                x={node.cx + 16}
                y={node.cy + NODE_HEIGHT / 2 + 5}
                fontSize={16}
              >
                {style.icon}
              </text>
              {/* Label */}
              <text
                x={node.cx + 38}
                y={node.cy + NODE_HEIGHT / 2 + 1}
                fill="#e5e7eb"
                fontSize={12}
                fontWeight={500}
                dominantBaseline="middle"
              >
                {node.label.length > 28
                  ? node.label.slice(0, 28) + "…"
                  : node.label}
              </text>
              {/* Kind badge */}
              <text
                x={node.cx + NODE_WIDTH - 12}
                y={node.cy + 14}
                fill={style.border}
                fontSize={8}
                textAnchor="end"
                opacity={0.7}
              >
                {node.type}
              </text>

              {/* Tooltip on hover */}
              {isHovered && node.data && (
                <foreignObject
                  x={node.cx + NODE_WIDTH + 8}
                  y={node.cy}
                  width={220}
                  height={100}
                >
                  <div
                    className="bg-zinc-800 border border-zinc-600 rounded-lg p-2 text-xs text-zinc-300 shadow-lg"
                    style={{ fontSize: 11 }}
                  >
                    {Object.entries(node.data as Record<string, unknown>)
                      .slice(0, 5)
                      .map(([k, v]) => (
                        <div key={k}>
                          <span className="text-zinc-500">{k}:</span>{" "}
                          {String(v)}
                        </div>
                      ))}
                  </div>
                </foreignObject>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
