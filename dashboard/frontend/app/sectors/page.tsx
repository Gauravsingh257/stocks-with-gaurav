/**
 * /sectors — Sector Rotation (Phase 2, validation).
 *
 * A server gate in front of a client view. The view reads precomputed rows from
 * /api/sectors/* — nothing is calculated on a page request.
 *
 * Gate, decided by the backend flags (see dashboard/backend/routes/sectors.py):
 *   - SECTOR_ROTATION_API_ENABLED off       → 404
 *   - SECTOR_ROTATION_PAGE_PUBLIC off       → 404 unless ?preview=1
 *   - backend unreachable / slow            → render anyway; the client shows its
 *                                             own error state. A backend blip must
 *                                             never turn into a 404.
 * noindex throughout Phase 2; not linked from navigation yet.
 */
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getBackendBase } from "@/lib/api";
import SectorRotation from "./SectorRotation";

export const metadata: Metadata = {
  title: "Sector Rotation",
  description:
    "Relative strength, momentum and breadth of NSE sectors, measured from every liquid stock in the universe. Analytics only.",
  robots: { index: false, follow: false },
};

type Status = { api_enabled: boolean; page_public: boolean };

async function fetchStatus(): Promise<Status | null> {
  const base = getBackendBase();
  if (!base) return null;
  const request = fetch(`${base}/api/sectors/status`, { cache: "no-store" })
    .then((r) => (r.ok ? (r.json() as Promise<Status>) : null))
    .catch(() => null);
  // Render budget via Promise.race, not AbortSignal (see lib/seo/stockData.ts).
  const budget = new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000));
  return Promise.race([request, budget]);
}

export default async function SectorsPage({
  searchParams,
}: {
  searchParams: Promise<{ preview?: string }>;
}) {
  const [status, params] = await Promise.all([fetchStatus(), searchParams]);
  if (status) {
    if (!status.api_enabled) notFound();
    if (!status.page_public && params.preview !== "1") notFound();
  }
  return (
    <div className="px-4 md:px-6 pt-4 pb-10">
      <SectorRotation />
    </div>
  );
}
