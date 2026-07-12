/**
 * lib/priceBandPrimitive.ts
 * A lightweight-charts series primitive that draws a horizontal, full-width
 * price BAND (shaded rectangle between two prices) — used to mark SMC zones
 * (Order Blocks, FVGs, Weekly OB/FVG) directly on the chart instead of only
 * listing them in the side panel.
 *
 * lightweight-charts v5 has no native rectangle; this implements the minimal
 * primitive surface (attached / updateAllViews / paneViews → renderer.draw)
 * and paints beneath the candles via zOrder "bottom".
 */
import type { ISeriesApi, SeriesType } from "lightweight-charts";

export interface BandOptions {
  top: number;
  bottom: number;
  fill: string;   // translucent fill, e.g. "rgba(0,209,140,0.12)"
  border: string; // boundary line colour
  label?: string;
}

// Structural subset of lightweight-charts' CanvasRenderingTarget2D — avoids a
// fragile deep import while staying fully typed (no `any`).
interface MediaScope {
  context: CanvasRenderingContext2D;
  mediaSize: { width: number; height: number };
}
interface DrawTarget {
  useMediaCoordinateSpace(cb: (scope: MediaScope) => void): void;
}

class BandRenderer {
  constructor(
    private readonly yTop: number | null,
    private readonly yBottom: number | null,
    private readonly fill: string,
    private readonly border: string,
  ) {}

  draw(target: DrawTarget): void {
    const yt = this.yTop;
    const yb = this.yBottom;
    if (yt === null || yb === null) return;
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      const w = scope.mediaSize.width;
      const y1 = Math.min(yt, yb);
      const y2 = Math.max(yt, yb);
      ctx.save();
      ctx.fillStyle = this.fill;
      ctx.fillRect(0, y1, w, Math.max(1, y2 - y1));
      ctx.strokeStyle = this.border;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, y1); ctx.lineTo(w, y1);
      ctx.moveTo(0, y2); ctx.lineTo(w, y2);
      ctx.stroke();
      ctx.restore();
    });
  }
}

class BandPaneView {
  private series: ISeriesApi<SeriesType> | null = null;
  private yTop: number | null = null;
  private yBottom: number | null = null;

  constructor(private readonly opts: BandOptions) {}

  setSeries(series: ISeriesApi<SeriesType>): void {
    this.series = series;
  }

  update(): void {
    if (!this.series) return;
    this.yTop = this.series.priceToCoordinate(this.opts.top);
    this.yBottom = this.series.priceToCoordinate(this.opts.bottom);
  }

  renderer() {
    return new BandRenderer(this.yTop, this.yBottom, this.opts.fill, this.opts.border);
  }

  zOrder() {
    return "bottom" as const;
  }
}

export class PriceBand {
  private readonly paneView: BandPaneView;

  constructor(opts: BandOptions) {
    this.paneView = new BandPaneView(opts);
  }

  attached(param: { series: ISeriesApi<SeriesType> }): void {
    this.paneView.setSeries(param.series);
  }

  updateAllViews(): void {
    this.paneView.update();
  }

  paneViews() {
    return [this.paneView];
  }
}
