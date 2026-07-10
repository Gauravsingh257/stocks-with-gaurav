"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Optional homepage switch (Part 12). When NEXT_PUBLIC_PIL_HOMEPAGE=1 the root
 * route redirects to the Portfolio Intelligence dashboard, making PIL the
 * platform homepage. Kept behind its OWN flag (separate from the nav flag) so
 * enabling PIL never silently hijacks the public marketing landing page / SEO.
 * Renders nothing.
 */
export default function PilHomeRedirect() {
  const router = useRouter();
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_PIL_HOMEPAGE === "1") {
      router.replace("/intelligence");
    }
  }, [router]);
  return null;
}
