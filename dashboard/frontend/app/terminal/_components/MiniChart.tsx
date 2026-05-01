"use client";

import { useMemo } from "react";

interface Props {
  data: number[];
  direction: "BUY" | "SELL";
  width?: number;
  height?: number;
}

/** Premium animated sparkline with gradient fill and entry/target glow. */
export default function MiniChart({ data, direction, width = 220, height = 70 }: Props) {
  const { path, area, color, glow } = useMemo(() => {
    if (!data?.length) return { path: "", area: "", color: "#00d4ff", glow: "rgba(0,212,255,0.25)" };
    const min = Math.min(...data);
    const max = Math.max(...data);
    const span = max - min || 1;
    const n = data.length;
    const pts = data.map((v, i) => {
      const x = (i / (n - 1)) * width;
      const y = height - ((v - min) / span) * (height - 6) - 3;
      return [x, y] as const;
    });
    const linePath = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
    const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`;
    const isUp = direction === "BUY";
    return {
      path: linePath,
      area: areaPath,
      color: isUp ? "#00e096" : "#ff4757",
      glow: isUp ? "rgba(0,224,150,0.35)" : "rgba(255,71,87,0.35)",
    };
  }, [data, direction, width, height]);

  if (!path) return <div style={{ height }} />;

  const gradientId = `mc-grad-${direction}-${data.length}`;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ display: "block" }}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.45" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
        <filter id={`${gradientId}-glow`}>
          <feGaussianBlur stdDeviation="1.6" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        filter={`url(#${gradientId}-glow)`}
        style={{
          filter: `drop-shadow(0 0 6px ${glow})`,
        }}
      />
    </svg>
  );
}
