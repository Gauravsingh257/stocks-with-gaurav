/**
 * Homepage Sector Rotation widget (Phase 3) — a SERVER component.
 *
 * A static, visual overview of where sectors are moving, drawn from the existing
 * cached /api/sectors/rotation payload (whole-market benchmark, daily, 5-session
 * trail). Nothing is calculated here beyond placing points on an SVG, and no
 * JavaScript ships to the browser: the payload is read on the server and only
 * the rendered SVG reaches the page. The whole card links to the full map.
 *
 * Any failure — flag off, backend slow or down, empty data — renders `fallback`
 * (the homepage's original preview), so the homepage can never break on it.
 * Fetches are cached (ISR) and budgeted with Promise.race, never AbortSignal,
 * which would opt the route out of caching (see lib/seo/stockData.ts).
 */
import type { ReactNode } from "react";
import { getBackendBase, type SectorQuadrant, type SectorRotationResponse } from "@/lib/api";
import { longDate } from "@/app/sectors/format";
import { fittedExtent, notability, placeLabels, smoothPath } from "@/app/sectors/mapGeometry";

const ROTATION_PATH = "/api/sectors/rotation?tf=D&benchmark=market&trail=5";
const STATUS_REVALIDATE_SEC = 300;
const ROTATION_REVALIDATE_SEC = 600;
const RENDER_BUDGET_MS = 2500;
const HREF = "/sectors?preview=1";

// The public homepage is dark-only (.public-home hardcodes its palette), so the
// widget uses fixed colours matching it rather than the dashboard theme tokens.
const COLOR: Record<SectorQuadrant, string> = {
  leading: "#00e096",
  improving: "#00d4ff",
  weakening: "#ffb020",
  lagging: "#ff5c7a",
};
const LABEL: Record<SectorQuadrant, string> = {
  leading: "Leading",
  improving: "Improving",
  weakening: "Weakening",
  lagging: "Lagging",
};
const ORDER: SectorQuadrant[] = ["leading", "improving", "weakening", "lagging"];

async function fetchJson<T>(path: string, revalidate: number): Promise<T | null> {
  const base = getBackendBase();
  if (!base) return null;
  const request = fetch(`${base}${path}`, { next: { revalidate } })
    .then((r) => (r.ok ? (r.json() as Promise<T>) : null))
    .catch(() => null);
  const budget = new Promise<null>((resolve) => setTimeout(() => resolve(null), RENDER_BUDGET_MS));
  return Promise.race([request, budget]);
}

export default async function SectorRotationWidget({ fallback }: { fallback: ReactNode }) {
  const [status, data] = await Promise.all([
    fetchJson<{ homepage_widget?: boolean }>("/api/sectors/status", STATUS_REVALIDATE_SEC),
    fetchJson<SectorRotationResponse>(ROTATION_PATH, ROTATION_REVALIDATE_SEC),
  ]);
  if (!status?.homepage_widget || !data?.sectors?.length) return fallback;

  const plot = data.sectors.filter((s) => s.plottable && s.trail.length > 0 && s.quadrant);
  if (plot.length < 5) return fallback;

  const byQuad = ORDER.map((q) => ({
    q,
    list: plot.filter((s) => s.quadrant === q).sort((a, b) => (b.rs_ratio ?? 0) - (a.rs_ratio ?? 0)),
  }));
  const f = data.freshness;
  const stale = f?.status === "stale";
  const asOf = longDate(data.as_of);
  const summary = byQuad.map(({ q, list }) => `${LABEL[q]}: ${list.map((s) => s.sector).join(", ") || "none"}`).join(". ");

  // Two static renders — wide and phone — switched by CSS, because the server
  // cannot know the screen width and one viewBox scaled to ~300px makes labels
  // unreadably small.
  const wide = renderMap(plot, { W: 560, H: 360, fs: 12.5, dotR: 5.5, maxLabels: 8, axis: "Relative strength vs whole market →", summary });
  const phone = renderMap(plot, { W: 340, H: 270, fs: 12, dotR: 5, maxLabels: 5, axis: "Rel. strength →", summary });

  return (
    <a
      href={HREF}
      className="public-product-visual sr-widget"
      aria-label={`Where NSE sectors are moving, relative to the whole market, as of the ${asOf} close. Open the sector rotation map.`}
    >
      <div className="preview-topline">
        <span>Sector rotation · vs whole market</span>
        <span className={stale ? "sr-stale" : "preview-live-dot"}>
          {stale ? `Data from ${asOf} · ${f.sessions_behind} session${f.sessions_behind === 1 ? "" : "s"} behind` : `Daily · ${asOf} close`}
        </span>
      </div>
      <div className="sr-widget-title">Where sectors are moving</div>

      <div className="sr-widget-map">
        <div className="sr-map-wide">{wide}</div>
        <div className="sr-map-phone">{phone}</div>
      </div>

      <div className="preview-grid sr-widget-quads">
        {byQuad.map(({ q, list }) => (
          <div key={q}>
            <span style={{ color: COLOR[q] }}>{LABEL[q]} · {list.length}</span>
            <strong>{list.slice(0, 2).map((s) => s.sector).join(", ") || "None"}</strong>
          </div>
        ))}
      </div>

      <div className="sr-widget-foot">
        <span>
          Relative strength and momentum of {plot.length} NSE sectors against the equal-weight market. Describes
          recent movement — not a forecast or a recommendation.
        </span>
        <span className="sr-widget-cta">Explore sectors →</span>
      </div>
    </a>
  );
}

type MapOpts = { W: number; H: number; fs: number; dotR: number; maxLabels: number; axis: string; summary: string };

function renderMap(plot: SectorRotationResponse["sectors"], o: MapOpts) {
  const { W, H, fs } = o;
  const m = { l: 16, r: 16, t: 14, b: 24 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const dom = fittedExtent(plot);
  const sx = (x: number) => m.l + ((x - (100 - dom)) / (2 * dom)) * pw;
  const sy = (y: number) => m.t + ((100 + dom - y) / (2 * dom)) * ph;
  const cx = sx(100);
  const cy = sy(100);
  const heads = plot.map((s) => {
    const t = s.trail[s.trail.length - 1];
    return { s, x: sx(t.rs_ratio), y: sy(t.rs_momentum) };
  });
  const labels = placeLabels({
    heads: heads.map((h) => ({ name: h.s.sector, x: h.x, y: h.y, priority: notability(h.s) })),
    active: null,
    fs,
    bounds: { l: m.l, r: W - m.r, t: m.t, b: H - m.b },
    dotRadius: o.dotR + 1,
    maxLabels: o.maxLabels,
  });
  const qfs = fs - 1.5;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Sector rotation map. ${o.summary}.`}>
      <rect x={m.l} y={m.t} width={cx - m.l} height={cy - m.t} fill={COLOR.improving} fillOpacity={0.06} />
      <rect x={cx} y={m.t} width={W - m.r - cx} height={cy - m.t} fill={COLOR.leading} fillOpacity={0.06} />
      <rect x={m.l} y={cy} width={cx - m.l} height={H - m.b - cy} fill={COLOR.lagging} fillOpacity={0.06} />
      <rect x={cx} y={cy} width={W - m.r - cx} height={H - m.b - cy} fill={COLOR.weakening} fillOpacity={0.06} />
      <line x1={cx} x2={cx} y1={m.t} y2={H - m.b} stroke="#aab8cf" strokeOpacity={0.35} strokeWidth={1.2} />
      <line x1={m.l} x2={W - m.r} y1={cy} y2={cy} stroke="#aab8cf" strokeOpacity={0.35} strokeWidth={1.2} />
      {(["improving", "leading", "lagging", "weakening"] as const).map((q) => {
        const left = q === "improving" || q === "lagging";
        const top = q === "improving" || q === "leading";
        return (
          <text key={q} x={left ? m.l + 7 : W - m.r - 7} y={top ? m.t + qfs + 4 : H - m.b - 7}
            textAnchor={left ? "start" : "end"} fontSize={qfs} fontWeight={800} letterSpacing="0.08em"
            fill={COLOR[q]} fillOpacity={0.75}>
            {LABEL[q].toUpperCase()}
          </text>
        );
      })}
      <text x={W - m.r} y={H - 6} textAnchor="end" fontSize={qfs} fontWeight={600} fill="#7f8da5">{o.axis}</text>
      {plot.map((s) => (
        <path key={`t-${s.sector}`} d={smoothPath(s.trail.map((t) => [sx(t.rs_ratio), sy(t.rs_momentum)] as [number, number]))}
          fill="none" stroke={COLOR[s.quadrant!]} strokeOpacity={0.4} strokeWidth={1.4} strokeLinecap="round"
          strokeDasharray={s.confidence === "low" ? "3 3" : undefined} />
      ))}
      {heads.map((h) => (
        <circle key={`h-${h.s.sector}`} cx={h.x} cy={h.y} r={o.dotR} strokeWidth={1.8}
          fill={h.s.confidence === "low" ? "#0b1322" : COLOR[h.s.quadrant!]}
          stroke={h.s.confidence === "low" ? COLOR[h.s.quadrant!] : "#0b1322"} />
      ))}
      {labels.map((l) => (
        <text key={`l-${l.sector}`} x={l.lx} y={l.ly} textAnchor={l.anchor} fontSize={fs} fontWeight={650}
          fill="#e8f4ff" stroke="#0b1322" strokeWidth={3} strokeLinejoin="round" paintOrder="stroke">
          {l.sector}
        </text>
      ))}
    </svg>
  );
}
