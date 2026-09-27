"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { SectorRotationRow } from "@/lib/api";
import { QUADRANT_LABEL, QUADRANT_VAR, num } from "./format";
import styles from "./sectors.module.css";

type Props = {
  sectors: SectorRotationRow[];
  selected: string | null;
  onSelect: (sector: string) => void;
  benchmarkShort: string;
  unitLabel: string;
};

const COMPACT_BELOW = 560;

function niceStep(span: number): number {
  const raw = span / 4;
  for (const s of [0.25, 0.5, 1, 2, 2.5, 5]) if (raw <= s) return s;
  return 10;
}

function smoothPath(p: [number, number][]): string {
  if (p.length < 2) return "";
  let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[i - 1] ?? p[i];
    const p1 = p[i];
    const p2 = p[i + 1];
    const p3 = p[i + 2] ?? p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

export default function RotationMap({ sectors, selected, onSelect, benchmarkShort, unitLabel }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 800));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const compact = width < COMPACT_BELOW;
  const W = compact ? 360 : 640;
  const H = compact ? 320 : 440;
  const m = compact ? { l: 34, r: 10, t: 12, b: 30 } : { l: 48, r: 16, t: 14, b: 40 };
  const fs = compact ? 10 : 11.5;

  const plot = useMemo(() => sectors.filter((s) => s.plottable && s.trail.length > 0), [sectors]);

  const { dom, step } = useMemo(() => {
    let maxDev = 1;
    for (const s of plot)
      for (const t of s.trail) maxDev = Math.max(maxDev, Math.abs(t.rs_ratio - 100), Math.abs(t.rs_momentum - 100));
    const st = niceStep(maxDev * 2);
    return { dom: Math.ceil((maxDev * 1.08) / (st / 2)) * (st / 2), step: st };
  }, [plot]);

  const sx = (x: number) => m.l + ((x - (100 - dom)) / (2 * dom)) * (W - m.l - m.r);
  const sy = (y: number) => H - m.b - ((y - (100 - dom)) / (2 * dom)) * (H - m.t - m.b);
  const cx = sx(100);
  const cy = sy(100);

  const ticks: number[] = [];
  for (let k = -Math.floor(dom / step + 1e-9); k <= Math.floor(dom / step + 1e-9); k++) ticks.push(100 + k * step);

  // Draw the selected sector last so it sits on top.
  const ordered = [...plot].sort((a, b) => Number(a.sector === selected) - Number(b.sector === selected));

  // Labels avoid every sector's head dot as well as each other: try right, then
  // left, at increasing vertical offsets; fall back to the first slot.
  type Box = [number, number, number, number];
  const r0 = compact ? 5 : 7;
  const placed: Box[] = plot.map((s) => {
    const t = s.trail[s.trail.length - 1];
    const x = sx(t.rs_ratio);
    const y = sy(t.rs_momentum);
    return [x - r0, y - r0, x + r0, y + r0];
  });
  const labelFor = (s: SectorRotationRow, hx: number, hy: number) => {
    if (compact && selected !== s.sector) return null;
    const tw = s.sector.length * (compact ? 5.6 : 6.4) + 4;
    const own: Box = [hx - r0, hy - r0, hx + r0, hy + r0];
    const hit = (b: Box) => placed.some((p) => p !== own && !(p[0] === own[0] && p[1] === own[1])
      && !(b[2] < p[0] || b[0] > p[2] || b[3] < p[1] || b[1] > p[3]));
    const slot = (anchor: "start" | "end", off: number) => {
      const lx = anchor === "start" ? hx + 9 : hx - 9;
      const ly = hy + 4 + off;
      const box: Box = anchor === "start" ? [lx, ly - fs, lx + tw, ly + 2] : [lx - tw, ly - fs, lx, ly + 2];
      const inside = box[0] >= m.l && box[2] <= W - m.r && box[1] >= m.t && box[3] <= H - m.b;
      return { lx, ly, anchor, box, ok: inside && !hit(box) };
    };
    const tries: ["start" | "end", number][] = [];
    for (const off of [0, -12, 12, -22, 22, -32, 32]) tries.push(["start", off], ["end", off]);
    const chosen = tries.map(([a, o]) => slot(a, o)).find((c) => c.ok) ?? slot(hx + tw > W - m.r ? "end" : "start", 0);
    placed.push(chosen.box);
    return { lx: chosen.lx, ly: chosen.ly, anchor: chosen.anchor };
  };

  const quadRects = [
    { q: "improving" as const, x: m.l, y: m.t, w: cx - m.l, h: cy - m.t, lx: m.l + 8, ly: m.t + fs + 6, a: "start" as const },
    { q: "leading" as const, x: cx, y: m.t, w: W - m.r - cx, h: cy - m.t, lx: W - m.r - 8, ly: m.t + fs + 6, a: "end" as const },
    { q: "lagging" as const, x: m.l, y: cy, w: cx - m.l, h: H - m.b - cy, lx: m.l + 8, ly: H - m.b - 8, a: "start" as const },
    { q: "weakening" as const, x: cx, y: cy, w: W - m.r - cx, h: H - m.b - cy, lx: W - m.r - 8, ly: H - m.b - 8, a: "end" as const },
  ];

  return (
    <div ref={wrap} className={styles.mapWrap}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Sector rotation map, relative strength ${benchmarkShort}. ${plot.length} sectors.`}
      >
        {quadRects.map((r) => (
          <rect key={r.q} x={r.x} y={r.y} width={Math.max(0, r.w)} height={Math.max(0, r.h)}
            style={{ fill: QUADRANT_VAR[r.q], fillOpacity: 0.06 }} />
        ))}
        {ticks.map((v) => {
          const center = Math.abs(v - 100) < 1e-9;
          const stroke = center ? "var(--text-dim)" : "var(--border)";
          const lbl = Number.isInteger(v) ? String(v) : v.toFixed(step < 0.5 ? 2 : 1);
          return (
            <g key={v}>
              <line x1={sx(v)} x2={sx(v)} y1={m.t} y2={H - m.b} style={{ stroke, strokeOpacity: center ? 0.6 : 1 }} />
              <line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} style={{ stroke, strokeOpacity: center ? 0.6 : 1 }} />
              <text x={sx(v)} y={H - m.b + (compact ? 13 : 16)} textAnchor="middle" fontSize={fs - 1.5}
                style={{ fill: "var(--text-dim)" }}>{lbl}</text>
              <text x={m.l - 6} y={sy(v) + 3} textAnchor="end" fontSize={fs - 1.5}
                style={{ fill: "var(--text-dim)" }}>{lbl}</text>
            </g>
          );
        })}
        {!compact && (
          <>
            <text x={W - m.r} y={H - 8} textAnchor="end" fontSize={11} fontWeight={600}
              style={{ fill: "var(--text-secondary)" }}>
              Relative strength {benchmarkShort} →
            </text>
            <text x={13} y={m.t} transform={`rotate(-90 13 ${m.t})`} textAnchor="end" fontSize={11} fontWeight={600}
              style={{ fill: "var(--text-secondary)" }}>
              Momentum of relative strength →
            </text>
          </>
        )}
        {quadRects.map((r) => (
          <text key={`l-${r.q}`} x={r.lx} y={r.ly} textAnchor={r.a} fontSize={fs} fontWeight={800}
            letterSpacing="0.06em" style={{ fill: QUADRANT_VAR[r.q], fillOpacity: 0.85 }}>
            {QUADRANT_LABEL[r.q].toUpperCase()}
          </text>
        ))}

        {ordered.map((s) => {
          const color = QUADRANT_VAR[s.quadrant ?? "lagging"];
          const dim = selected !== null && selected !== s.sector;
          const low = s.confidence === "low";
          const P = s.trail.map((t) => [sx(t.rs_ratio), sy(t.rs_momentum)] as [number, number]);
          const [hx, hy] = P[P.length - 1];
          const label = labelFor(s, hx, hy);
          const describe = `${s.sector}: ${QUADRANT_LABEL[s.quadrant ?? "lagging"]}, relative strength ${num(s.rs_ratio, 2)}, momentum ${num(s.rs_momentum, 2)}${low ? ", low confidence" : ""}`;
          return (
            <g
              key={s.sector}
              className={styles.sectorG}
              tabIndex={0}
              role="button"
              aria-label={describe}
              aria-pressed={selected === s.sector}
              opacity={dim ? 0.18 : 1}
              onClick={() => onSelect(s.sector)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(s.sector);
                }
              }}
            >
              <title>{describe}</title>
              <path d={smoothPath(P)} fill="none" strokeWidth={compact ? 1.4 : 1.8} strokeLinecap="round"
                strokeDasharray={low ? "4 3" : undefined} style={{ stroke: color, strokeOpacity: 0.55 }} />
              {P.slice(0, -1).map(([x, y], i) => (
                <circle key={i} cx={x} cy={y} r={compact ? 1.6 : 2.2}
                  style={{ fill: color, fillOpacity: 0.2 + 0.6 * (i / P.length) }} />
              ))}
              <circle cx={hx} cy={hy} r={compact ? 9 : 12} style={{ fill: color, fillOpacity: 0.12 }} />
              <circle className={styles.head} cx={hx} cy={hy} r={compact ? 4.5 : 6} strokeWidth={2}
                style={low
                  ? { fill: "var(--bg-surface)", stroke: color }
                  : { fill: color, stroke: "var(--bg-surface)" }} />
              <circle cx={hx} cy={hy} r={16} fill="transparent" />
              {label && (
                <text x={label.lx} y={label.ly} textAnchor={label.anchor} fontSize={fs} fontWeight={700}
                  strokeWidth={3} strokeLinejoin="round" paintOrder="stroke"
                  style={{ fill: "var(--text-primary)", stroke: "var(--bg-surface)" }}>
                  {s.sector}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <span className="sr-only">{`Each dot is one ${unitLabel}; the large dot is the latest reading.`}</span>
    </div>
  );
}
