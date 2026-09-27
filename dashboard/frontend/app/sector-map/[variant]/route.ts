/**
 * /sector-map/wide and /sector-map/phone — the homepage widget's rotation map as
 * a cached SVG image (Phase 3). Statically generated and revalidated with the
 * rotation data (10 min); never computed per visitor. Reads the existing cached
 * /api/sectors/rotation payload only.
 */
import { MAP_VARIANTS, type MapVariant, fetchRotation, plottable, renderSectorMapSvg, unavailableSvg } from "@/lib/sectorRotationServer";

export const revalidate = 600;
export const dynamicParams = false;

export function generateStaticParams() {
  return (Object.keys(MAP_VARIANTS) as MapVariant[]).map((variant) => ({ variant }));
}

export async function GET(_req: Request, ctx: { params: Promise<{ variant: string }> }) {
  const { variant } = await ctx.params;
  const v = (variant in MAP_VARIANTS ? variant : "wide") as MapVariant;
  const plot = plottable(await fetchRotation());
  const body = plot.length >= 5 ? renderSectorMapSvg(plot, v) : unavailableSvg(v);
  return new Response(body, {
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      "Cache-Control": "public, max-age=300, s-maxage=600, stale-while-revalidate=3600",
    },
  });
}
