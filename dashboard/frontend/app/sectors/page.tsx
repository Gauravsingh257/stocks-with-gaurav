/**
 * /sectors — Sector Rotation (public).
 *
 * A server gate in front of a client view. The view reads precomputed rows from
 * /api/sectors/* — nothing is calculated on a page request.
 *
 * The gate follows the backend flags (dashboard/backend/routes/sectors.py) and is
 * cached for 5 minutes, so the page stays statically served:
 *   - backend says the API or the page is switched off → 404 (kill switch)
 *   - backend unreachable / slow → render anyway; the client shows its own error
 *     state. A backend blip must never turn an indexed page into a 404.
 */
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getBackendBase } from "@/lib/api";
import SectorRotation from "./SectorRotation";

export const metadata: Metadata = {
  title: "NSE Sector Rotation — Relative Strength & Breadth",
  description:
    "Where NSE sectors are moving: relative strength, momentum, breadth and 52-week highs for 22 sectors, measured from every liquid stock and updated after each close. Analytics, not advice.",
  alternates: { canonical: "/sectors" },
};

type Status = { api_enabled: boolean; page_public: boolean };

async function fetchStatus(): Promise<Status | null> {
  const base = getBackendBase();
  if (!base) return null;
  const request = fetch(`${base}/api/sectors/status`, { next: { revalidate: 300 } })
    .then((r) => (r.ok ? (r.json() as Promise<Status>) : null))
    .catch(() => null);
  // Render budget via Promise.race, not AbortSignal (see lib/seo/stockData.ts).
  const budget = new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000));
  return Promise.race([request, budget]);
}

export default async function SectorsPage() {
  const status = await fetchStatus();
  if (status && (!status.api_enabled || !status.page_public)) notFound();
  return (
    <div className="px-4 md:px-6 pt-4 pb-10">
      <SectorRotation />
    </div>
  );
}
