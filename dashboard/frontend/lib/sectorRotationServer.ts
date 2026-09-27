/**
 * Server-only helpers for the homepage Sector Rotation widget (Phase 3):
 * cached reads of the existing API, and the static SVG map rendered as a string.
 *
 * The map is served as an image (app/sector-map/[variant]/route.ts) rather than
 * inline SVG: a server-rendered SVG is emitted twice — once as HTML and again in
 * the RSC payload — which tripled the homepage's size. As an image it is fetched
 * once, cached by the CDN and the browser, and only the visitor's variant loads.
 */
import { getBackendBase, type SectorQuadrant, type SectorRotationResponse } from "@/lib/api";
import { fittedExtent, notability, placeLabels, smoothPath } from "@/app/sectors/mapGeometry";

export const ROTATION_PATH = "/api/sectors/rotation?tf=D&benchmark=market&trail=5";
export const STATUS_REVALIDATE_SEC = 300;
export const ROTATION_REVALIDATE_SEC = 600;
const RENDER_BUDGET_MS = 2500;

/** Cached (ISR) fetch with a render budget. Promise.race, never AbortSignal —
 *  a signal opts the fetch out of caching (see lib/seo/stockData.ts). */
export async function fetchJson<T>(path: string, revalidate: number): Promise<T | null> {
  const base = getBackendBase();
  if (!base) return null;
  const request = fetch(`${base}${path}`, { next: { revalidate } })
    .then((r) => (r.ok ? (r.json() as Promise<T>) : null))
    .catch(() => null);
  const budget = new Promise<null>((resolve) => setTimeout(() => resolve(null), RENDER_BUDGET_MS));
  return Promise.race([request, budget]);
}

export const fetchRotation = () => fetchJson<SectorRotationResponse>(ROTATION_PATH, ROTATION_REVALIDATE_SEC);
export const fetchSectorStatus = () =>
  fetchJson<{ homepage_widget?: boolean }>("/api/sectors/status", STATUS_REVALIDATE_SEC);

export function plottable(data: SectorRotationResponse | null) {
  return (data?.sectors ?? []).filter((s) => s.plottable && s.trail.length > 0 && s.quadrant);
}

// The public homepage is dark-only (.public-home hardcodes its palette).
export const QUAD_COLOR: Record<SectorQuadrant, string> = {
  leading: "#00e096",
  improving: "#00d4ff",
  weakening: "#ffb020",
  lagging: "#ff5c7a",
};
export const QUAD_LABEL: Record<SectorQuadrant, string> = {
  leading: "Leading",
  improving: "Improving",
  weakening: "Weakening",
  lagging: "Lagging",
};

export const MAP_VARIANTS = {
  wide: { W: 560, H: 360, fs: 12.5, dotR: 5.5, maxLabels: 8, axis: "Relative strength vs whole market →" },
  phone: { W: 340, H: 270, fs: 12, dotR: 5, maxLabels: 5, axis: "Rel. strength →" },
} as const;
export type MapVariant = keyof typeof MAP_VARIANTS;

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const n = (v: number) => Math.round(v * 10) / 10;

/** The widget map as a standalone SVG document. */
export function renderSectorMapSvg(plot: SectorRotationResponse["sectors"], variant: MapVariant): string {
  const o = MAP_VARIANTS[variant];
  const { W, H, fs } = o;
  const m = { l: 16, r: 16, t: 14, b: 24 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const dom = fittedExtent(plot);
  const sx = (x: number) => m.l + ((x - (100 - dom)) / (2 * dom)) * pw;
  const sy = (y: number) => m.t + ((100 + dom - y) / (2 * dom)) * ph;
  const cx = n(sx(100));
  const cy = n(sy(100));
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
  const C = QUAD_COLOR;
  const parts: string[] = [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="Inter, system-ui, -apple-system, Segoe UI, sans-serif">`,
    `<rect width="${W}" height="${H}" fill="#050b16"/>`,
    `<rect x="${m.l}" y="${m.t}" width="${n(cx - m.l)}" height="${n(cy - m.t)}" fill="${C.improving}" fill-opacity="0.06"/>`,
    `<rect x="${cx}" y="${m.t}" width="${n(W - m.r - cx)}" height="${n(cy - m.t)}" fill="${C.leading}" fill-opacity="0.06"/>`,
    `<rect x="${m.l}" y="${cy}" width="${n(cx - m.l)}" height="${n(H - m.b - cy)}" fill="${C.lagging}" fill-opacity="0.06"/>`,
    `<rect x="${cx}" y="${cy}" width="${n(W - m.r - cx)}" height="${n(H - m.b - cy)}" fill="${C.weakening}" fill-opacity="0.06"/>`,
    `<g stroke="#aab8cf" stroke-opacity="0.35" stroke-width="1.2"><line x1="${cx}" x2="${cx}" y1="${m.t}" y2="${H - m.b}"/><line x1="${m.l}" x2="${W - m.r}" y1="${cy}" y2="${cy}"/></g>`,
  ];
  (["improving", "leading", "lagging", "weakening"] as const).forEach((q) => {
    const left = q === "improving" || q === "lagging";
    const top = q === "improving" || q === "leading";
    parts.push(`<text x="${left ? m.l + 7 : W - m.r - 7}" y="${n(top ? m.t + qfs + 4 : H - m.b - 7)}" text-anchor="${left ? "start" : "end"}" font-size="${qfs}" font-weight="800" letter-spacing="0.08em" fill="${C[q]}" fill-opacity="0.75">${QUAD_LABEL[q].toUpperCase()}</text>`);
  });
  parts.push(`<text x="${W - m.r}" y="${H - 6}" text-anchor="end" font-size="${qfs}" font-weight="600" fill="#7f8da5">${esc(o.axis)}</text>`);
  for (const s of plot) {
    const d = smoothPath(s.trail.map((t) => [sx(t.rs_ratio), sy(t.rs_momentum)] as [number, number]));
    const dash = s.confidence === "low" ? ` stroke-dasharray="3 3"` : "";
    parts.push(`<path d="${d}" fill="none" stroke="${C[s.quadrant!]}" stroke-opacity="0.4" stroke-width="1.4" stroke-linecap="round"${dash}/>`);
  }
  for (const h of heads) {
    const c = C[h.s.quadrant!];
    const low = h.s.confidence === "low";
    parts.push(`<circle cx="${n(h.x)}" cy="${n(h.y)}" r="${o.dotR}" stroke-width="1.8" fill="${low ? "#0b1322" : c}" stroke="${low ? c : "#0b1322"}"/>`);
  }
  for (const l of labels) {
    parts.push(`<text x="${n(l.lx)}" y="${n(l.ly)}" text-anchor="${l.anchor}" font-size="${fs}" font-weight="650" fill="#e8f4ff" stroke="#0b1322" stroke-width="3" stroke-linejoin="round" paint-order="stroke">${esc(l.sector)}</text>`);
  }
  parts.push("</svg>");
  return parts.join("");
}

/** Neutral map shown if the data is unavailable when the image is generated. */
export function unavailableSvg(variant: MapVariant): string {
  const { W, H } = MAP_VARIANTS[variant];
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="Inter, system-ui, sans-serif"><rect width="${W}" height="${H}" fill="#050b16"/><line x1="${W / 2}" x2="${W / 2}" y1="14" y2="${H - 24}" stroke="#aab8cf" stroke-opacity="0.2"/><line x1="16" x2="${W - 16}" y1="${H / 2}" y2="${H / 2}" stroke="#aab8cf" stroke-opacity="0.2"/><text x="${W / 2}" y="${H / 2 - 12}" text-anchor="middle" font-size="13" fill="#7f8da5">Sector map is updating</text></svg>`;
}
