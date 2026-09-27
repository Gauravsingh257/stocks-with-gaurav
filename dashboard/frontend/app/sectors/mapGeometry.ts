/**
 * Pure rotation-map geometry shared by the interactive /sectors map and the
 * server-rendered homepage widget. No React, no DOM — safe on server and client.
 */
import type { SectorQuadrant, SectorRotationRow } from "@/lib/api";

export type Box = [number, number, number, number];

export function smoothPath(p: [number, number][]): string {
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

export function quadOf(x: number, y: number): SectorQuadrant {
  if (x >= 100) return y >= 100 ? "leading" : "weakening";
  return y >= 100 ? "improving" : "lagging";
}

/** Symmetric half-extent around 100 that fits every trail point (+8% margin). */
export function fittedExtent(sectors: SectorRotationRow[]): number {
  let maxDev = 1;
  for (const s of sectors)
    for (const t of s.trail) maxDev = Math.max(maxDev, Math.abs(t.rs_ratio - 100), Math.abs(t.rs_momentum - 100));
  return Math.ceil(maxDev * 1.08 * 4) / 4;
}

/** Label priority: distance from the 100/100 centre, plus a bonus for sectors
 *  whose trail changed quadrant. */
export function notability(s: SectorRotationRow): number {
  const a = s.trail[0];
  const b = s.trail[s.trail.length - 1];
  const moved = quadOf(a.rs_ratio, a.rs_momentum) !== quadOf(b.rs_ratio, b.rs_momentum) ? 1.5 : 0;
  return Math.hypot(b.rs_ratio - 100, b.rs_momentum - 100) + moved;
}

export type LabelHead = { name: string; x: number; y: number; priority: number };
export type PlacedLabel = { sector: string; lx: number; ly: number; anchor: "start" | "end" };

/**
 * Place labels beside their own dots, highest priority first, skipping any that
 * cannot fit without touching another label or dot. `active` is always labelled
 * (with a fallback slot if needed). Coordinates are viewBox units.
 */
export function placeLabels(opts: {
  heads: LabelHead[];
  active: string | null;
  fs: number;
  bounds: { l: number; r: number; t: number; b: number };
  dotRadius: number;
  maxLabels?: number;
}): PlacedLabel[] {
  const { heads, active, fs, bounds, dotRadius: r0, maxLabels = Infinity } = opts;
  const inView = heads.filter((h) => h.x >= bounds.l && h.x <= bounds.r && h.y >= bounds.t && h.y <= bounds.b);
  const placed: Box[] = inView.map((h) => [h.x - r0, h.y - r0, h.x + r0, h.y + r0]);
  const order = [...inView].sort((a, b) => {
    if (a.name === active) return -1;
    if (b.name === active) return 1;
    return b.priority - a.priority;
  });
  const out: PlacedLabel[] = [];
  for (const h of order) {
    if (out.length >= maxLabels && h.name !== active) break;
    // ~0.62em per character for the 650-weight label face (measured against
    // rendered text; a smaller estimate let compact labels overlap).
    const tw = h.name.length * fs * 0.62 + 4;
    const isOwn = (p: Box) => Math.abs(p[0] - (h.x - r0)) < 0.01 && Math.abs(p[1] - (h.y - r0)) < 0.01;
    const blocked = (b: Box) => placed.some((p) => !isOwn(p) && !(b[2] < p[0] || b[0] > p[2] || b[3] < p[1] || b[1] > p[3]));
    const slot = (anchor: "start" | "end", off: number) => {
      const lx = anchor === "start" ? h.x + 9 : h.x - 9;
      const ly = h.y + 4 + off;
      // Padded for descenders and the 3px outline stroke drawn behind each label.
      const box: Box = anchor === "start"
        ? [lx - 1.5, ly - fs - 1, lx + tw + 1.5, ly + 4]
        : [lx - tw - 1.5, ly - fs - 1, lx + 1.5, ly + 4];
      const inside = box[0] >= bounds.l && box[2] <= bounds.r && box[1] >= bounds.t && box[3] <= bounds.b;
      return { lx, ly, anchor, box, ok: inside && !blocked(box) };
    };
    const isActive = h.name === active;
    let chosen: ReturnType<typeof slot> | null = null;
    for (const o of isActive ? [0, -11, 11, -20, 20] : [0, -10, 10]) {
      for (const a of ["start", "end"] as const) {
        const c = slot(a, o);
        if (c.ok) { chosen = c; break; }
      }
      if (chosen) break;
    }
    if (!chosen && isActive) chosen = slot(h.x + tw + 12 > bounds.r ? "end" : "start", 0);
    if (!chosen) continue;
    placed.push(chosen.box);
    out.push({ sector: h.name, lx: chosen.lx, ly: chosen.ly, anchor: chosen.anchor });
  }
  return out;
}
