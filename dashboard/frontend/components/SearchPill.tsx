"use client";

import { useSyncExternalStore } from "react";
import { Search } from "lucide-react";
import { openGlobalSearch, prewarmSearchIndex } from "@/components/CommandPalette";

/**
 * The visible entry point to global search. It replaces a bare 32px icon that
 * sat, styled exactly like the theme toggle, in a crowded status strip — only 6
 * of 153 visitors used search in its first two months. md+ gets a labelled pill
 * with the shortcut; small screens keep a ≥44px icon so the top bar still fits.
 * Hover or focus starts loading the stock list, so typing is answered instantly.
 */

// The platform never changes during a visit, so there is nothing to subscribe to.
const subscribeNoop = () => () => {};

function isApplePlatform(): boolean {
  try {
    const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
    const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || "";
    return /mac|iphone|ipad|ipod/i.test(platform);
  } catch {
    return false;
  }
}

export default function SearchPill() {
  // Server snapshot is "not Apple", so SSR and hydration agree on "Ctrl K";
  // React then re-reads the client value without a hydration mismatch.
  const apple = useSyncExternalStore(subscribeNoop, isApplePlatform, () => false);
  const shortcut = apple ? "⌘K" : "Ctrl K";

  const tint = {
    background: "rgba(34,211,238,0.06)",
    border: "1px solid rgba(34,211,238,0.30)",
    cursor: "pointer",
  } as const;

  return (
    <>
      <button
        type="button"
        onClick={() => openGlobalSearch("topbar_pill")}
        onPointerEnter={prewarmSearchIndex}
        onFocus={prewarmSearchIndex}
        title="Search stocks, sectors & pages"
        aria-label="Search stocks, sectors and pages"
        aria-keyshortcuts="Control+K Meta+K"
        className="hidden md:flex items-center gap-2 h-11 lg:h-8 px-2.5 rounded-md shrink-0"
        style={{ ...tint, color: "var(--text-secondary)" }}
      >
        <Search size={14} className="shrink-0" style={{ color: "var(--accent)" }} />
        <span className="hidden xl:inline text-xs whitespace-nowrap">Search stocks, sectors, pages…</span>
        <span className="xl:hidden text-xs whitespace-nowrap">Search</span>
        <kbd
          className="text-[10px] leading-none rounded px-1.5 py-1 whitespace-nowrap"
          style={{ border: "1px solid var(--border)", color: "var(--text-dim)" }}
        >
          {shortcut}
        </kbd>
      </button>
      <button
        type="button"
        onClick={() => openGlobalSearch("topbar_icon")}
        onPointerEnter={prewarmSearchIndex}
        onFocus={prewarmSearchIndex}
        title="Search stocks, sectors & pages"
        aria-label="Search stocks, sectors and pages"
        className="md:hidden grid place-items-center w-11 h-11 rounded-md shrink-0"
        style={{ ...tint, color: "var(--accent)" }}
      >
        <Search size={18} />
      </button>
    </>
  );
}
