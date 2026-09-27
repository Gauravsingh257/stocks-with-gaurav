"use client";
/**
 * Rotation map (Phase 2.1).
 *
 * Every sector's latest reading is always drawn; labels are placed by priority
 * (focused sector, then the most extreme readings and quadrant transitions) and
 * only where they fit cleanly beside their own dot — zooming in makes room for
 * more. Hover (mouse) or tap (touch) shows a sector card; click, or a second
 * tap, opens the detail drawer. Hit-testing picks the NEAREST dot, so crowded
 * clusters stay usable.
 *
 * Navigation: + / − / Fit buttons; Ctrl/⌘ + wheel or trackpad pinch zooms at the
 * cursor (a plain wheel scrolls the page — the map never traps scrolling);
 * touch pinch zooms, drag pans once zoomed; keyboard + − 0 and arrows.
 * Zoom is limited to 1×–8× and panning stays within the fitted extent.
 */
import { Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SectorQuadrant, SectorRotationRow } from "@/lib/api";
import { QUADRANT_LABEL, QUADRANT_VAR, num, pct, signed } from "./format";
import styles from "./sectors.module.css";

type Props = {
  sectors: SectorRotationRow[];
  /** Sector whose drawer is open (emphasised). */
  focus: string | null;
  onOpen: (sector: string) => void;
  benchmarkShort: string;
  unitLabel: string;
};

type View = { cx: number; cy: number; k: number };
type Box = [number, number, number, number];

// Phones only: a laptop-width map card must keep its labels.
const COMPACT_BELOW = 440;
const MAX_ZOOM = 8;

function niceStep(span: number): number {
  const raw = span / 5;
  for (const s of [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10]) if (raw <= s) return s;
  return 20;
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

function quadOf(x: number, y: number): SectorQuadrant {
  if (x >= 100) return y >= 100 ? "leading" : "weakening";
  return y >= 100 ? "improving" : "lagging";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function shortDate(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

export default function RotationMap({ sectors, focus, onOpen, benchmarkShort, unitLabel }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [cssWidth, setCssWidth] = useState(800);
  const [view, setView] = useState<View>({ cx: 100, cy: 100, k: 1 });
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [hint, setHint] = useState(false);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setCssWidth(entries[0]?.contentRect.width ?? 800));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const compact = cssWidth < COMPACT_BELOW;
  const W = compact ? 360 : 640;
  const H = compact ? 330 : 460;
  const m = useMemo(() => (compact ? { l: 34, r: 10, t: 12, b: 30 } : { l: 50, r: 16, t: 14, b: 42 }), [compact]);
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const fs = compact ? 10.5 : 11.5;

  const plot = useMemo(() => sectors.filter((s) => s.plottable && s.trail.length > 0), [sectors]);

  // Fitted extent: symmetric around 100 so the quadrant cross is centred at 1×.
  const dom = useMemo(() => {
    let maxDev = 1;
    for (const s of plot)
      for (const t of s.trail) maxDev = Math.max(maxDev, Math.abs(t.rs_ratio - 100), Math.abs(t.rs_momentum - 100));
    return Math.ceil(maxDev * 1.08 * 4) / 4;
  }, [plot]);

  const clamp = useCallback((v: View): View => {
    const k = Math.min(MAX_ZOOM, Math.max(1, v.k));
    const lim = dom - dom / k;
    return {
      k,
      cx: Math.min(100 + lim, Math.max(100 - lim, v.cx)),
      cy: Math.min(100 + lim, Math.max(100 - lim, v.cy)),
    };
  }, [dom]);
  const cur = clamp(view);
  const half = dom / cur.k;
  const x0 = cur.cx - half;
  const x1 = cur.cx + half;
  const y0 = cur.cy - half;
  const y1 = cur.cy + half;
  const sx = useCallback((x: number) => m.l + ((x - x0) / (x1 - x0)) * pw, [m.l, x0, x1, pw]);
  const sy = useCallback((y: number) => m.t + ((y1 - y) / (y1 - y0)) * ph, [m.t, y0, y1, ph]);

  const heads = useMemo(() => plot.map((s) => {
    const t = s.trail[s.trail.length - 1];
    return { s, x: sx(t.rs_ratio), y: sy(t.rs_momentum) };
  }), [plot, sx, sy]);

  const active = hovered ?? pinned ?? focus;

  // ── coordinate helpers ───────────────────────────────────────────────────
  const toViewBox = (clientX: number, clientY: number) => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r || !r.width) return { vx: 0, vy: 0, scale: 1 };
    return { vx: ((clientX - r.left) * W) / r.width, vy: ((clientY - r.top) * H) / r.height, scale: W / r.width };
  };
  const nearest = (vx: number, vy: number, radiusCss: number, scale: number) => {
    let best: string | null = null;
    let bd = radiusCss * scale;
    for (const h of heads) {
      const d = Math.hypot(h.x - vx, h.y - vy);
      if (d <= bd) { bd = d; best = h.s.sector; }
    }
    return best;
  };

  const zoomAt = useCallback((factor: number, vx?: number, vy?: number) => {
    setView((prev) => {
      const p = clamp(prev);
      const k = Math.min(MAX_ZOOM, Math.max(1, p.k * factor));
      if (k === p.k) return p;
      const fx = ((vx ?? m.l + pw / 2) - m.l) / pw;
      const fy = ((vy ?? m.t + ph / 2) - m.t) / ph;
      const h0 = dom / p.k;
      const dataX = p.cx - h0 + fx * 2 * h0;
      const dataY = p.cy + h0 - fy * 2 * h0;
      const h1 = dom / k;
      return clamp({ k, cx: dataX - fx * 2 * h1 + h1, cy: dataY + fy * 2 * h1 - h1 });
    });
  }, [clamp, dom, m.l, m.t, pw, ph]);
  const reset = useCallback(() => setView({ cx: 100, cy: 100, k: 1 }), []);
  const panBy = useCallback((dxView: number, dyView: number) => {
    setView((prev) => {
      const p = clamp(prev);
      const h0 = dom / p.k;
      return clamp({ ...p, cx: p.cx - (dxView / pw) * 2 * h0, cy: p.cy + (dyView / ph) * 2 * h0 });
    });
  }, [clamp, dom, pw, ph]);

  // Wheel / Safari gesture listeners must be non-passive to stop page zoom.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    let hintTimer: ReturnType<typeof setTimeout> | undefined;
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const r = el.getBoundingClientRect();
        zoomAt(Math.exp(-e.deltaY * 0.01), ((e.clientX - r.left) * W) / r.width, ((e.clientY - r.top) * H) / r.height);
      } else {
        setHint(true);
        clearTimeout(hintTimer);
        hintTimer = setTimeout(() => setHint(false), 1400);
      }
    };
    let last = 1;
    const onGestureStart = (e: Event) => { e.preventDefault(); last = 1; };
    const onGestureChange = (e: Event) => {
      e.preventDefault();
      const scale = (e as unknown as { scale: number }).scale || 1;
      zoomAt(scale / last);
      last = scale;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("gesturestart", onGestureStart, { passive: false });
    el.addEventListener("gesturechange", onGestureChange, { passive: false });
    return () => {
      clearTimeout(hintTimer);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("gesturestart", onGestureStart);
      el.removeEventListener("gesturechange", onGestureChange);
    };
  }, [zoomAt, W, H]);

  // ── pointer handling: pan, pinch, hover, tap ─────────────────────────────
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ moved: boolean; startDist: number; origin: { x: number; y: number }; lastMid: { x: number; y: number } | null }>(
    { moved: false, startDist: 0, origin: { x: 0, y: 0 }, lastMid: null });

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try {
      svgRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* pointer already released (fast tap) — capture is only a convenience */
    }
    const pts = [...pointers.current.values()];
    const two = pts.length === 2;
    gesture.current = {
      moved: two,
      origin: { x: e.clientX, y: e.clientY },
      startDist: two ? Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y) : 0,
      lastMid: two ? { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 } : null,
    };
  };

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const { vx, vy, scale } = toViewBox(e.clientX, e.clientY);
    const prev = pointers.current.get(e.pointerId);
    if (!prev) {
      if (e.pointerType === "mouse") setHovered(nearest(vx, vy, 16, scale));
      return;
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    const g = gesture.current;
    if (pts.length === 2) {
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      const mid = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      if (g.startDist > 0) {
        const c = toViewBox(mid.x, mid.y);
        zoomAt(dist / g.startDist, c.vx, c.vy);
      }
      if (g.lastMid) panBy((mid.x - g.lastMid.x) * scale, (mid.y - g.lastMid.y) * scale);
      g.startDist = dist;
      g.lastMid = mid;
      return;
    }
    if (!g.moved && Math.hypot(e.clientX - g.origin.x, e.clientY - g.origin.y) > 5) g.moved = true;
    if (g.moved && cur.k > 1) {
      setDragging(true);
      panBy((e.clientX - prev.x) * scale, (e.clientY - prev.y) * scale);
    }
  };

  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const wasSingle = pointers.current.size === 1;
    pointers.current.delete(e.pointerId);
    setDragging(false);
    if (!wasSingle || gesture.current.moved || e.type === "pointercancel") return;
    const { vx, vy, scale } = toViewBox(e.clientX, e.clientY);
    const touch = e.pointerType !== "mouse";
    const hit = nearest(vx, vy, touch ? 24 : 16, scale);
    if (!touch) {
      if (hit) onOpen(hit);
      return;
    }
    if (!hit) setPinned(null);
    else if (pinned === hit) onOpen(hit);
    else setPinned(hit);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = 40;
    if (e.key === "+" || e.key === "=") zoomAt(1.4);
    else if (e.key === "-" || e.key === "_") zoomAt(1 / 1.4);
    else if (e.key === "0") reset();
    else if (e.key === "ArrowLeft") panBy(step, 0);
    else if (e.key === "ArrowRight") panBy(-step, 0);
    else if (e.key === "ArrowUp") panBy(0, step);
    else if (e.key === "ArrowDown") panBy(0, -step);
    else if (e.key === "Escape") setPinned(null);
    else return;
    e.preventDefault();
  };

  // ── labels: priority order, only where they fit beside their own dot ─────
  const labels = useMemo(() => {
    const r0 = compact ? 5 : 6.5;
    const inView = heads.filter((h) => h.x >= m.l && h.x <= W - m.r && h.y >= m.t && h.y <= H - m.b);
    const placed: Box[] = inView.map((h) => [h.x - r0, h.y - r0, h.x + r0, h.y + r0]);
    const notability = (s: SectorRotationRow) => {
      const a = s.trail[0];
      const b = s.trail[s.trail.length - 1];
      const moved = quadOf(a.rs_ratio, a.rs_momentum) !== quadOf(b.rs_ratio, b.rs_momentum) ? 1.5 : 0;
      return Math.hypot(b.rs_ratio - 100, b.rs_momentum - 100) + moved;
    };
    const order = [...inView].sort((a, b) => {
      if (a.s.sector === active) return -1;
      if (b.s.sector === active) return 1;
      return notability(b.s) - notability(a.s);
    });
    const out: { sector: string; lx: number; ly: number; anchor: "start" | "end" }[] = [];
    for (const h of order) {
      // ~0.62em per character for the 650-weight label face (measured against
      // rendered text; a smaller estimate let compact labels overlap).
      const tw = h.s.sector.length * fs * 0.62 + 4;
      const isOwn = (p: Box) => Math.abs(p[0] - (h.x - r0)) < 0.01 && Math.abs(p[1] - (h.y - r0)) < 0.01;
      const blocked = (b: Box) => placed.some((p) => !isOwn(p) && !(b[2] < p[0] || b[0] > p[2] || b[3] < p[1] || b[1] > p[3]));
      const slot = (anchor: "start" | "end", off: number) => {
        const lx = anchor === "start" ? h.x + 9 : h.x - 9;
        const ly = h.y + 4 + off;
        // Padded for descenders and the 3px outline stroke drawn behind each label.
        const box: Box = anchor === "start"
          ? [lx - 1.5, ly - fs - 1, lx + tw + 1.5, ly + 4]
          : [lx - tw - 1.5, ly - fs - 1, lx + 1.5, ly + 4];
        const inside = box[0] >= m.l && box[2] <= W - m.r && box[1] >= m.t && box[3] <= H - m.b;
        return { lx, ly, anchor, box, ok: inside && !blocked(box) };
      };
      const isActive = h.s.sector === active;
      let chosen: ReturnType<typeof slot> | null = null;
      for (const o of isActive ? [0, -11, 11, -20, 20] : [0, -10, 10]) {
        for (const a of ["start", "end"] as const) {
          const c = slot(a, o);
          if (c.ok) { chosen = c; break; }
        }
        if (chosen) break;
      }
      if (!chosen && isActive) chosen = slot(h.x + tw + 12 > W - m.r ? "end" : "start", 0);
      if (!chosen) continue;
      placed.push(chosen.box);
      out.push({ sector: h.s.sector, lx: chosen.lx, ly: chosen.ly, anchor: chosen.anchor });
    }
    return out;
  }, [heads, active, compact, fs, W, H, m]);

  // ── static geometry ──────────────────────────────────────────────────────
  const step = niceStep(x1 - x0);
  const fmtTick = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(step < 0.5 ? 2 : 1));
  const xTicks: number[] = [];
  for (let v = Math.ceil(x0 / step) * step; v <= x1 + 1e-9; v += step) xTicks.push(Number(v.toFixed(4)));
  const yTicks: number[] = [];
  for (let v = Math.ceil(y0 / step) * step; v <= y1 + 1e-9; v += step) yTicks.push(Number(v.toFixed(4)));

  const clipX = (v: number) => Math.min(W - m.r, Math.max(m.l, v));
  const clipY = (v: number) => Math.min(H - m.b, Math.max(m.t, v));
  const quads = (["improving", "leading", "lagging", "weakening"] as const).map((q) => {
    const left = q === "improving" || q === "lagging";
    const top = q === "improving" || q === "leading";
    const ax = clipX(left ? m.l : sx(100));
    const bx = clipX(left ? sx(100) : W - m.r);
    const ay = clipY(top ? m.t : sy(100));
    const by = clipY(top ? sy(100) : H - m.b);
    return { q, x: ax, y: ay, w: Math.max(0, bx - ax), h: Math.max(0, by - ay), left, top };
  });

  const activeRow = active ? plot.find((s) => s.sector === active) ?? null : null;
  const activeHead = activeRow ? heads.find((h) => h.s.sector === activeRow.sector) ?? null : null;
  const zoomed = cur.k > 1.001;

  // Card position in CSS pixels relative to the map wrapper (hover / tap only).
  const cssScale = cssWidth / W;
  const tip = !compact && activeRow && activeHead && (hovered || pinned)
    && activeHead.x >= m.l && activeHead.x <= W - m.r && activeHead.y >= m.t && activeHead.y <= H - m.b
    ? (() => {
      const x = activeHead.x * cssScale;
      const y = activeHead.y * cssScale;
      const flipX = x > cssWidth - 200;
      const flipY = y < 130;
      return { left: flipX ? x - 14 : x + 14, top: flipY ? y + 14 : y - 14, flipX, flipY };
    })()
    : null;

  const ariaLabel = `Sector rotation map, relative strength ${benchmarkShort}. ${plot.length} sectors.`;

  return (
    <div className={styles.mapShell}>
      <div className={styles.mapToolbar}>
        <span className={styles.mapHelp}>
          {compact ? "Tap a dot for details · pinch to zoom" : "Hover a dot for details · Ctrl/⌘ + scroll or pinch to zoom"}
        </span>
        <div className={styles.zoomGroup} role="group" aria-label="Map zoom">
          <button type="button" onClick={() => zoomAt(1.5)} disabled={cur.k >= MAX_ZOOM} aria-label="Zoom in" title="Zoom in (+)">
            <Plus size={15} aria-hidden="true" />
          </button>
          <button type="button" onClick={() => zoomAt(1 / 1.5)} disabled={!zoomed} aria-label="Zoom out" title="Zoom out (−)">
            <Minus size={15} aria-hidden="true" />
          </button>
          <button type="button" onClick={reset} disabled={!zoomed} aria-label="Reset view" title="Fit all sectors (0)">
            <Maximize2 size={14} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div
        ref={wrapRef}
        className={styles.mapWrap}
        tabIndex={0}
        role="group"
        aria-label={`${ariaLabel} Plus and minus zoom, arrow keys pan, 0 resets.`}
        onKeyDown={onKeyDown}
      >
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={ariaLabel}
          style={{
            touchAction: zoomed ? "none" : "pan-y",
            cursor: dragging ? "grabbing" : hovered ? "pointer" : zoomed ? "grab" : "default",
          }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={(e) => { if (e.pointerType === "mouse" && !pointers.current.size) setHovered(null); }}
        >
          <defs>
            <clipPath id="rotation-plot">
              <rect x={m.l} y={m.t} width={pw} height={ph} />
            </clipPath>
          </defs>

          {quads.map((r) => r.w > 0 && r.h > 0 && (
            <rect key={r.q} x={r.x} y={r.y} width={r.w} height={r.h} style={{ fill: QUADRANT_VAR[r.q], fillOpacity: 0.055 }} />
          ))}
          {xTicks.map((v) => (
            <g key={`x${v}`}>
              <line x1={sx(v)} x2={sx(v)} y1={m.t} y2={H - m.b} style={{ stroke: "var(--border)" }} />
              <text x={sx(v)} y={H - m.b + (compact ? 13 : 16)} textAnchor="middle" fontSize={fs - 1.5}
                style={{ fill: "var(--text-dim)" }}>{fmtTick(v)}</text>
            </g>
          ))}
          {yTicks.map((v) => (
            <g key={`y${v}`}>
              <line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} style={{ stroke: "var(--border)" }} />
              <text x={m.l - 6} y={sy(v) + 3} textAnchor="end" fontSize={fs - 1.5}
                style={{ fill: "var(--text-dim)" }}>{fmtTick(v)}</text>
            </g>
          ))}
          {/* The 100 lines are the quadrant boundaries — stronger than the grid. */}
          {x0 <= 100 && x1 >= 100 && (
            <line x1={sx(100)} x2={sx(100)} y1={m.t} y2={H - m.b} strokeWidth={1.2}
              style={{ stroke: "var(--text-secondary)", strokeOpacity: 0.55 }} />
          )}
          {y0 <= 100 && y1 >= 100 && (
            <line x1={m.l} x2={W - m.r} y1={sy(100)} y2={sy(100)} strokeWidth={1.2}
              style={{ stroke: "var(--text-secondary)", strokeOpacity: 0.55 }} />
          )}
          <text x={W - m.r} y={H - (compact ? 4 : 8)} textAnchor="end" fontSize={compact ? 9.5 : 11} fontWeight={600}
            style={{ fill: "var(--text-secondary)" }}>
            {compact ? "Rel. strength →" : `Relative strength ${benchmarkShort} →`}
          </text>
          <text x={compact ? 10 : 13} y={m.t} transform={`rotate(-90 ${compact ? 10 : 13} ${m.t})`} textAnchor="end"
            fontSize={compact ? 9.5 : 11} fontWeight={600} style={{ fill: "var(--text-secondary)" }}>
            {compact ? "Momentum →" : "Momentum of relative strength →"}
          </text>
          {quads.map((r) => r.w > 70 && r.h > 26 && (
            <text key={`l-${r.q}`} x={r.left ? r.x + 8 : r.x + r.w - 8} y={r.top ? r.y + fs + 6 : r.y + r.h - 8}
              textAnchor={r.left ? "start" : "end"} fontSize={fs} fontWeight={800} letterSpacing="0.06em"
              style={{ fill: QUADRANT_VAR[r.q], fillOpacity: 0.8 }}>
              {QUADRANT_LABEL[r.q].toUpperCase()}
            </text>
          ))}

          <g clipPath="url(#rotation-plot)">
            {/* Trails are quiet by default; the active sector's trail is drawn last, in full. */}
            {plot.filter((s) => s.sector !== active).map((s) => (
              <path key={`t-${s.sector}`}
                d={smoothPath(s.trail.map((t) => [sx(t.rs_ratio), sy(t.rs_momentum)] as [number, number]))}
                fill="none" strokeWidth={compact ? 1.1 : 1.3} strokeLinecap="round"
                strokeDasharray={s.confidence === "low" ? "3 3" : undefined}
                style={{ stroke: QUADRANT_VAR[s.quadrant ?? "lagging"], strokeOpacity: active ? 0.1 : 0.3 }} />
            ))}
            {heads.map((h) => {
              const color = QUADRANT_VAR[h.s.quadrant ?? "lagging"];
              const low = h.s.confidence === "low";
              return (
                <circle key={`h-${h.s.sector}`} cx={h.x} cy={h.y} r={compact ? 4.5 : 5.5} strokeWidth={1.8}
                  opacity={active !== null && active !== h.s.sector ? 0.35 : 1}
                  style={low ? { fill: "var(--bg-surface)", stroke: color } : { fill: color, stroke: "var(--bg-surface)" }} />
              );
            })}
            {activeRow && activeHead && (() => {
              const color = QUADRANT_VAR[activeRow.quadrant ?? "lagging"];
              const P = activeRow.trail.map((t) => [sx(t.rs_ratio), sy(t.rs_momentum)] as [number, number]);
              return (
                <g pointerEvents="none">
                  <path d={smoothPath(P)} fill="none" strokeWidth={2.4} strokeLinecap="round"
                    style={{ stroke: color, strokeOpacity: 0.95 }} />
                  {P.slice(0, -1).map(([x, y], i) => (
                    <circle key={i} cx={x} cy={y} r={2.6} style={{ fill: color, fillOpacity: 0.35 + 0.6 * (i / P.length) }} />
                  ))}
                  <text x={P[0][0]} y={P[0][1] - 7} textAnchor="middle" fontSize={fs - 2} strokeWidth={3} paintOrder="stroke"
                    style={{ fill: "var(--text-secondary)", stroke: "var(--bg-surface)" }}>
                    {shortDate(activeRow.trail[0].date)}
                  </text>
                  <circle cx={activeHead.x} cy={activeHead.y} r={compact ? 10 : 12} style={{ fill: color, fillOpacity: 0.18 }} />
                  <circle cx={activeHead.x} cy={activeHead.y} r={compact ? 5 : 6.5} strokeWidth={2}
                    style={activeRow.confidence === "low"
                      ? { fill: "var(--bg-surface)", stroke: color } : { fill: color, stroke: "var(--text-primary)" }} />
                </g>
              );
            })()}
            {labels.map((l) => (
              <text key={`lab-${l.sector}`} x={l.lx} y={l.ly} textAnchor={l.anchor} fontSize={fs}
                fontWeight={l.sector === active ? 800 : 650} strokeWidth={3} strokeLinejoin="round" paintOrder="stroke"
                pointerEvents="none"
                style={{ fill: "var(--text-primary)", stroke: "var(--bg-surface)", opacity: active && l.sector !== active ? 0.55 : 1 }}>
                {l.sector}
              </text>
            ))}
          </g>
        </svg>

        {tip && activeRow && (
          <div
            className={styles.mapTip}
            style={{
              left: tip.left,
              top: tip.top,
              transform: `translate(${tip.flipX ? "-100%" : "0"}, ${tip.flipY ? "0" : "-100%"})`,
              pointerEvents: pinned ? "auto" : "none",
            }}
          >
            <div className={styles.mapTipTitle}>{activeRow.sector}</div>
            <div className={styles.mapTipQuad} style={{ color: QUADRANT_VAR[activeRow.quadrant ?? "lagging"] }}>
              {QUADRANT_LABEL[activeRow.quadrant ?? "lagging"]} {benchmarkShort}
            </div>
            <dl className={styles.mapTipGrid}>
              <dt>Rel. strength</dt><dd>{num(activeRow.rs_ratio, 2)}</dd>
              <dt>Momentum</dt><dd>{num(activeRow.rs_momentum, 2)}</dd>
              <dt>Acceleration</dt><dd>{signed(activeRow.rs_accel)}</dd>
              <dt>Above 50-day avg</dt><dd>{pct(activeRow.pct_above_50)}</dd>
            </dl>
            {activeRow.confidence === "low" && <div className={styles.mapTipNote}>Low-confidence reading</div>}
            {pinned ? (
              <button type="button" className={styles.mapTipBtn} onClick={() => onOpen(activeRow.sector)}>Details</button>
            ) : (
              <div className={styles.mapTipNote}>Click for details</div>
            )}
          </div>
        )}
        {hint && !zoomed && <div className={styles.mapHint}>Hold Ctrl (⌘ on Mac) and scroll to zoom</div>}
        <span className="sr-only">{`Each dot is one ${unitLabel}; the large dot is the latest reading.`}</span>
      </div>

      {/* Phones: the tapped sector's card sits under the map, never over it
          (a floating card collided with the fixed bottom navigation). */}
      {compact && pinned && activeRow && (
        <div className={styles.mapStrip} role="status">
          <div>
            <div className={styles.mapTipTitle}>{activeRow.sector}</div>
            <div className={styles.mapTipQuad} style={{ color: QUADRANT_VAR[activeRow.quadrant ?? "lagging"] }}>
              {QUADRANT_LABEL[activeRow.quadrant ?? "lagging"]} {benchmarkShort}
              {activeRow.confidence === "low" ? " · low confidence" : ""}
            </div>
          </div>
          <dl className={styles.mapStripGrid}>
            <div><dt>RS</dt><dd>{num(activeRow.rs_ratio, 1)}</dd></div>
            <div><dt>Mom.</dt><dd>{num(activeRow.rs_momentum, 1)}</dd></div>
            <div><dt>Accel.</dt><dd>{signed(activeRow.rs_accel, 1)}</dd></div>
            <div><dt>&gt;50D</dt><dd>{pct(activeRow.pct_above_50)}</dd></div>
          </dl>
          <button type="button" className={styles.mapTipBtn} onClick={() => onOpen(activeRow.sector)}>Details</button>
        </div>
      )}
    </div>
  );
}
