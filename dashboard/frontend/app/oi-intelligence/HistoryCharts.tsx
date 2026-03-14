"use client";
import { biasColor, fmt, pcrZone, type PCRHistoryPoint, type BiasHistoryPoint } from "./types";

export function PCRSparkline({ history }: { history: PCRHistoryPoint[] }) {
  if (history.length < 2) return null;

  const values = history.map(h => h.pcr);
  const min = Math.min(...values) - 0.05;
  const max = Math.max(...values) + 0.05;
  const range = max - min || 1;
  const w = 300;
  const h = 60;

  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x},${y}`;
  }).join(" ");

  const lastPcr = values[values.length - 1];
  const zone = pcrZone(lastPcr);

  return (
    <div className="glass" style={{ padding: "14px 20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: "0.65rem", color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600 }}>
          PCR TREND ({history.length} readings)
        </div>
        <div style={{ fontSize: "0.7rem", color: zone.color, fontFamily: "monospace", fontWeight: 600 }}>
          {fmt(lastPcr, 3)}
        </div>
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: 60 }}>
        <line x1="0" y1={h - ((1.2 - min) / range) * h} x2={w} y2={h - ((1.2 - min) / range) * h}
          stroke="rgba(0,224,150,0.2)" strokeDasharray="4,4" />
        <line x1="0" y1={h - ((0.7 - min) / range) * h} x2={w} y2={h - ((0.7 - min) / range) * h}
          stroke="rgba(255,71,87,0.2)" strokeDasharray="4,4" />
        <polyline points={points} fill="none" stroke={zone.color} strokeWidth="2"
          style={{ filter: `drop-shadow(0 0 4px ${zone.color})` }} />
      </svg>
    </div>
  );
}

export function BiasTimeline({ history }: { history: BiasHistoryPoint[] }) {
  if (history.length === 0) return null;

  return (
    <div className="glass" style={{ padding: "14px 20px" }}>
      <div style={{ fontSize: "0.65rem", color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600, marginBottom: 10 }}>
        BIAS HISTORY
      </div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {history.map((h, i) => (
          <div key={i} title={`${h.time} — ${h.bias} (${h.confidence}%)`} style={{
            width: 24, height: 24, borderRadius: 4,
            background: biasColor(h.bias) + "30",
            border: `1px solid ${biasColor(h.bias)}40`,
            display: "flex", alignItems: "center", justifyContent: "center",
            cursor: "default",
          }}>
            <div style={{
              width: 6, height: 6, borderRadius: "50%",
              background: biasColor(h.bias),
              opacity: h.confidence / 100,
            }} />
          </div>
        ))}
      </div>
    </div>
  );
}
