/**
 * lib/smcZonesPrimitive.ts
 * One lightweight-charts primitive that renders SMC zones AND their inline
 * labels so the chart explains itself (no cross-referencing the side legend).
 *
 *  • Bands (Order Block / FVG / Weekly) painted BEHIND candles (zOrder "bottom").
 *  • Single-price structure (CHoCH) drawn as a dashed line.
 *  • Compact rounded labels painted ABOVE candles (zOrder "top") with vertical
 *    collision-avoidance, colour-matched pills, white text, mobile abbreviations.
 *  • Hovered zone (from legend or crosshair) is emphasised.
 *
 * All geometry uses series.priceToCoordinate() per frame → tracks zoom/pan and
 * scales with the pane. Pure canvas: no DOM-per-label, no React re-renders.
 */
import type { ISeriesApi, SeriesType } from "lightweight-charts";

export interface SmcZone {
  id: string;
  label: string;   // full, e.g. "Order Block (Demand)"
  short: string;   // mobile, e.g. "OB (Dmd)"
  top: number;
  bottom: number;
  fill: string;    // translucent band fill
  border: string;  // solid accent + pill background
}

export interface ZoneOptions {
  showLabels: boolean;
  hoveredId: string | null;
  mobile: boolean;
}

interface MediaScope { context: CanvasRenderingContext2D; mediaSize: { width: number; height: number }; }
interface DrawTarget { useMediaCoordinateSpace(cb: (scope: MediaScope) => void): void; }

const LABEL_PAD_X = 6;
const LABEL_PAD_Y = 3;
const LABEL_LEFT = 8;

function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.max(0, Math.min(r, h / 2, w / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

class ZonesRenderer {
  constructor(
    private readonly kind: "bands" | "labels",
    private readonly series: ISeriesApi<SeriesType> | null,
    private readonly zones: SmcZone[],
    private readonly getOpts: () => ZoneOptions,
  ) {}

  draw(target: DrawTarget): void {
    const series = this.series;
    if (!series) return;
    const opts = this.getOpts();

    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = scope.mediaSize.width;
      const height = scope.mediaSize.height;

      const items = this.zones
        .map((z) => {
          const yTop = series.priceToCoordinate(z.top);
          const yBottom = series.priceToCoordinate(z.bottom);
          if (yTop === null || yBottom === null) return null;
          return { z, yTop: yTop as number, yBottom: yBottom as number };
        })
        .filter((v): v is { z: SmcZone; yTop: number; yBottom: number } => v !== null);

      if (this.kind === "bands") {
        for (const it of items) {
          const hovered = opts.hoveredId === it.z.id;
          if (Math.abs(it.yTop - it.yBottom) < 0.5) {
            ctx.save();
            ctx.strokeStyle = it.z.border;
            ctx.globalAlpha = hovered ? 1 : 0.9;
            ctx.lineWidth = hovered ? 2 : 1;
            ctx.setLineDash([4, 3]);
            ctx.beginPath();
            ctx.moveTo(0, it.yTop);
            ctx.lineTo(width, it.yTop);
            ctx.stroke();
            ctx.restore();
          } else {
            const y1 = Math.min(it.yTop, it.yBottom);
            const y2 = Math.max(it.yTop, it.yBottom);
            ctx.save();
            ctx.fillStyle = it.z.fill;
            ctx.globalAlpha = hovered ? 1 : 0.85;
            ctx.fillRect(0, y1, width, Math.max(1, y2 - y1));
            ctx.globalAlpha = 1;
            ctx.strokeStyle = it.z.border;
            ctx.lineWidth = hovered ? 2 : 1;
            ctx.beginPath();
            ctx.moveTo(0, y1); ctx.lineTo(width, y1);
            ctx.moveTo(0, y2); ctx.lineTo(width, y2);
            ctx.stroke();
            ctx.restore();
          }
        }
        return;
      }

      // ── labels ──────────────────────────────────────────────────────────
      if (!opts.showLabels || items.length === 0) return;
      const fontSize = opts.mobile ? 9 : 11;
      const labelH = fontSize + LABEL_PAD_Y * 2 + 2;
      ctx.font = `600 ${fontSize}px -apple-system, "Segoe UI", Roboto, sans-serif`;

      const entries = items
        .map((it) => {
          const text = opts.mobile ? it.z.short : it.z.label;
          return {
            z: it.z,
            target: (it.yTop + it.yBottom) / 2,
            y: (it.yTop + it.yBottom) / 2,
            text,
            w: ctx.measureText(text).width + LABEL_PAD_X * 2,
          };
        })
        .sort((a, b) => a.target - b.target);

      // collision-avoidance: push overlapping labels down, then clamp within the
      // pane, then relax upward so nothing sits off-screen or overlaps.
      const gap = labelH + 3;
      for (let i = 1; i < entries.length; i++) {
        if (entries[i].y < entries[i - 1].y + gap) entries[i].y = entries[i - 1].y + gap;
      }
      const maxY = height - labelH / 2 - 2;
      const minY = labelH / 2 + 2;
      const over = entries[entries.length - 1].y - maxY;
      if (over > 0) for (const e of entries) e.y -= over;
      for (let i = entries.length - 1; i > 0; i--) {
        if (entries[i].y - entries[i - 1].y < gap) entries[i - 1].y = entries[i].y - gap;
      }
      for (const e of entries) e.y = Math.max(minY, Math.min(maxY, e.y));

      for (const e of entries) {
        const hovered = opts.hoveredId === e.z.id;
        const h = labelH;
        const x = LABEL_LEFT;
        const yTopPill = e.y - h / 2;
        ctx.save();
        roundRectPath(ctx, x, yTopPill, e.w, h, 4);
        ctx.fillStyle = e.z.border;
        ctx.globalAlpha = hovered ? 1 : 0.88;
        ctx.fill();
        if (hovered) {
          ctx.globalAlpha = 1;
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#ffffff";
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        ctx.fillText(e.text, x + LABEL_PAD_X, e.y);
        ctx.restore();
      }
    });
  }
}

class ZonesView {
  private series: ISeriesApi<SeriesType> | null = null;
  constructor(
    private readonly kind: "bands" | "labels",
    private readonly zones: SmcZone[],
    private readonly getOpts: () => ZoneOptions,
  ) {}
  setSeries(series: ISeriesApi<SeriesType>): void { this.series = series; }
  renderer() { return new ZonesRenderer(this.kind, this.series, this.zones, this.getOpts); }
  zOrder() { return this.kind === "bands" ? ("bottom" as const) : ("top" as const); }
}

export class SmcZonesPrimitive {
  private readonly bands: ZonesView;
  private readonly labels: ZonesView;
  private req?: () => void;

  constructor(zones: SmcZone[], getOpts: () => ZoneOptions) {
    this.bands = new ZonesView("bands", zones, getOpts);
    this.labels = new ZonesView("labels", zones, getOpts);
  }
  attached(param: { series: ISeriesApi<SeriesType>; requestUpdate: () => void }): void {
    this.bands.setSeries(param.series);
    this.labels.setSeries(param.series);
    this.req = param.requestUpdate;
  }
  detached(): void { this.req = undefined; }
  updateAllViews(): void { /* renderers read the series live each draw */ }
  paneViews() { return [this.bands, this.labels]; }
  /** Repaint after options (labels/hover/mobile) change — no React re-render. */
  requestUpdate(): void { this.req?.(); }
}
