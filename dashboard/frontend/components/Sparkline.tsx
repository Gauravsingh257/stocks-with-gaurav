"use client";

import { memo, useMemo } from "react";

const SPARKLINE_WIDTH = 80;
const SPARKLINE_HEIGHT = 20;

interface SparklineProps {
  data: number[];
  className?: string;
  positive?: boolean;
}

function SparklineComponent({ data, className = "", positive = true }: SparklineProps) {
  const path = useMemo(() => {
    if (!data || !Array.isArray(data) || data.length < 2) return "";
    const min = Math.min(...data);
    const max = Math.max(...data);
    const normalized =
      max === min
        ? data.map(() => 0.5)
        : data.map((v) => (v - min) / (max - min));

    const width = SPARKLINE_WIDTH;
    const height = SPARKLINE_HEIGHT;
    const n = normalized.length;

    return normalized
      .map((v, i) => {
        const x = (i / (n - 1)) * width;
        const y = height - v * height;
        return `${i === 0 ? "M" : "L"} ${x} ${y}`;
      })
      .join(" ");
  }, [data]);

  if (!data || !Array.isArray(data) || data.length < 2) return null;

  return (
    <svg
      width={SPARKLINE_WIDTH}
      height={SPARKLINE_HEIGHT}
      className={`ml-2 opacity-80 hidden sm:inline-block ${className}`}
      aria-hidden
    >
      <path
        d={path}
        fill="none"
        stroke={positive ? "rgb(34, 197, 94)" : "rgb(239, 68, 68)"}
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default memo(SparklineComponent);
