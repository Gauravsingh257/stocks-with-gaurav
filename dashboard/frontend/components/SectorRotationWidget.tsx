/**
 * Homepage Sector Rotation widget (Phase 3) — a SERVER component.
 *
 * A static, visual overview of where sectors are moving, from the existing
 * cached /api/sectors/rotation payload (whole-market benchmark, daily, 5-session
 * trail). No JavaScript ships to the browser and the payload stays on the server.
 * The map itself is a cached SVG image (/sector-map/[variant]) so the homepage
 * HTML carries only the text parts; the whole card links to the full map.
 *
 * Any failure — flag off, backend slow or down, empty data — renders `fallback`
 * (the homepage's original preview), so the homepage can never break on it.
 */
import type { ReactNode } from "react";
import type { SectorQuadrant } from "@/lib/api";
import { longDate } from "@/app/sectors/format";
import {
  MAP_VARIANTS, QUAD_COLOR, QUAD_LABEL, fetchRotation, fetchSectorStatus, plottable,
} from "@/lib/sectorRotationServer";

const HREF = "/sectors?preview=1";
const ORDER: SectorQuadrant[] = ["leading", "improving", "weakening", "lagging"];

export default async function SectorRotationWidget({ fallback }: { fallback: ReactNode }) {
  const [status, data] = await Promise.all([fetchSectorStatus(), fetchRotation()]);
  if (!status?.homepage_widget || !data) return fallback;
  const plot = plottable(data);
  if (plot.length < 5) return fallback;

  const byQuad = ORDER.map((q) => ({
    q,
    list: plot.filter((s) => s.quadrant === q).sort((a, b) => (b.rs_ratio ?? 0) - (a.rs_ratio ?? 0)),
  }));
  const f = data.freshness;
  const stale = f?.status === "stale";
  const asOf = longDate(data.as_of);
  const alt = `Sector rotation map vs the whole market, ${asOf} close. `
    + byQuad.map(({ q, list }) => `${QUAD_LABEL[q]}: ${list.map((s) => s.sector).join(", ") || "none"}`).join(". ") + ".";
  // The data date versions the image URL so browsers never pair new text with an old map.
  const v = encodeURIComponent(data.as_of ?? "");
  const { wide, phone } = MAP_VARIANTS;

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
        <picture>
          <source media="(max-width: 560px)" srcSet={`/sector-map/phone?d=${v}`} width={phone.W} height={phone.H} />
          <img src={`/sector-map/wide?d=${v}`} width={wide.W} height={wide.H} alt={alt} decoding="async" />
        </picture>
      </div>

      <div className="preview-grid sr-widget-quads">
        {byQuad.map(({ q, list }) => (
          <div key={q}>
            <span style={{ color: QUAD_COLOR[q] }}>{QUAD_LABEL[q]} · {list.length}</span>
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
