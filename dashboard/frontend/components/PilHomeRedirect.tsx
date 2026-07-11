"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

/**
 * Home routing for the root path. Logged-in users are sent to their Command
 * Center (the default authenticated home, Sprint 1) — the public marketing page
 * stays for anonymous visitors / SEO. Renders nothing.
 *
 * The legacy NEXT_PUBLIC_PIL_HOMEPAGE=1 override still points power users at the
 * Portfolio dashboard if explicitly set.
 */
export default function PilHomeRedirect() {
  const router = useRouter();
  const { user, loading } = useAuth();
  useEffect(() => {
    if (loading) return;
    if (process.env.NEXT_PUBLIC_PIL_HOMEPAGE === "1") {
      router.replace("/intelligence");
    } else if (user) {
      router.replace("/command");
    }
  }, [router, user, loading]);
  return null;
}
