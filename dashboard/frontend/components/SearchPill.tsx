"use client";

import { useSyncExternalStore } from "react";
import { Search } from "lucide-react";
import { openGlobalSearch, prewarmSearchIndex } from "@/components/CommandPalette";

/**
 * The visible entry point to global search — the header's primary action.
 *
 * It replaced a bare 32px icon that sat, styled exactly like the theme toggle, in a
 * crowded status strip (only 6 of 153 visitors used search in its first two months).
 * The header places the two variants separately:
 *   - "pill": a wide labelled field with the shortcut, on md+
 *   - "icon": a ≥44px button beside the other actions, on small screens
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

export default function SearchPill({ variant = "pill" }: { variant?: "pill" | "icon" }) {
  // Server snapshot is "not Apple", so SSR and hydration agree on "Ctrl K";
  // React then re-reads the client value without a hydration mismatch.
  const apple = useSyncExternalStore(subscribeNoop, isApplePlatform, () => false);
  const shortcut = apple ? "⌘K" : "Ctrl K";

  if (variant === "icon") {
    return (
      <button
        type="button"
        onClick={() => openGlobalSearch("topbar_icon")}
        onPointerEnter={prewarmSearchIndex}
        onFocus={prewarmSearchIndex}
        title="Search stocks, sectors & pages"
        aria-label="Search stocks, sectors and pages"
        className="grid place-items-center w-11 h-11 rounded-md shrink-0 border border-cyan-400/30 bg-cyan-400/5"
        style={{ cursor: "pointer", color: "var(--accent)" }}
      >
        <Search size={18} />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => openGlobalSearch("topbar_pill")}
      onPointerEnter={prewarmSearchIndex}
      onFocus={prewarmSearchIndex}
      title="Search stocks, sectors & pages"
      aria-label="Search stocks, sectors and pages"
      aria-keyshortcuts="Control+K Meta+K"
      className="flex items-center gap-2.5 w-full max-w-[460px] h-11 lg:h-9 pl-3 pr-2 rounded-lg text-left border border-cyan-400/25 bg-cyan-400/5 transition-colors hover:border-cyan-400/50 hover:bg-cyan-400/10"
      style={{ cursor: "pointer", color: "var(--text-secondary)" }}
    >
      <Search size={15} className="shrink-0" style={{ color: "var(--accent)" }} />
      <span className="flex-1 min-w-0 truncate text-[0.8rem]">Search stocks, sectors, pages…</span>
      <kbd
        className="shrink-0 text-[10px] leading-none rounded px-1.5 py-1 whitespace-nowrap"
        style={{ border: "1px solid var(--border)", color: "var(--text-dim)" }}
      >
        {shortcut}
      </kbd>
    </button>
  );
}
