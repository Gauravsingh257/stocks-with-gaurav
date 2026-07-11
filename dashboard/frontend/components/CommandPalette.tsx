"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BarChart2, Bot, Eye, Search, Sparkles, Bookmark,
  LayoutDashboard, Radar, Globe, TrendingUp,
} from "lucide-react";
import { track } from "@/lib/analytics";

/** Fire this from anywhere (e.g. the TopBar search button) to open the palette. */
export const OPEN_SEARCH_EVENT = "swg:open-search";
export function openGlobalSearch() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(OPEN_SEARCH_EVENT));
}

type Cmd = { href: string; label: string; icon: typeof BarChart2; keywords?: string };

// Destinations mirror the 5 consolidated nav groups + their folded pages.
const PAGES: Cmd[] = [
  { href: "/terminal", label: "Terminal", icon: Sparkles, keywords: "home signals live" },
  { href: "/research", label: "AI Research", icon: Bot, keywords: "analyze ask ai discovery" },
  { href: "/screeners", label: "Screeners", icon: Radar, keywords: "scanner sector rotation supertrend" },
  { href: "/watchlist", label: "Watchlist", icon: Bookmark, keywords: "monitor alerts" },
  { href: "/intelligence", label: "Portfolio", icon: LayoutDashboard, keywords: "pms holdings book" },
  { href: "/analytics", label: "Track Record", icon: BarChart2, keywords: "performance analytics equity hit rate" },
  { href: "/oi-intelligence", label: "OI Intelligence", icon: Eye, keywords: "options pcr max pain markets" },
  { href: "/market-intelligence", label: "Market Intel", icon: Globe, keywords: "macro fx breadth markets" },
];

// A rough NSE ticker looks like 1–20 uppercase letters/&/-/digits.
const TICKER_RE = /^[A-Za-z][A-Za-z0-9&.\-]{0,19}$/;

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const router = useRouter();

  const q = query.trim();
  const pageMatches = useMemo(
    () =>
      q
        ? PAGES.filter(
            (p) =>
              p.label.toLowerCase().includes(q.toLowerCase()) ||
              p.href.toLowerCase().includes(q.toLowerCase()) ||
              (p.keywords ?? "").includes(q.toLowerCase()),
          )
        : PAGES,
    [q],
  );

  // A "search this symbol" action appears first whenever the query looks like a ticker.
  const symbol = q && TICKER_RE.test(q) ? q.toUpperCase() : null;
  const results: { href: string; label: string; icon: typeof BarChart2; hint?: string }[] = useMemo(() => {
    const out: { href: string; label: string; icon: typeof BarChart2; hint?: string }[] = [];
    if (symbol) out.push({ href: `/stock/${symbol}`, label: symbol, icon: TrendingUp, hint: "Open stock" });
    for (const p of pageMatches) out.push({ href: p.href, label: p.label, icon: p.icon, hint: "Page" });
    return out;
  }, [symbol, pageMatches]);

  const closePalette = useCallback(() => {
    setOpen(false);
    setQuery("");
    setSelected(0);
  }, []);

  const openPalette = useCallback(() => {
    setOpen(true);
    setQuery("");
    setSelected(0);
  }, []);

  // Global open triggers: Ctrl/Cmd+K and the custom event (from TopBar button).
  useEffect(() => {
    const onEvt = () => openPalette();
    window.addEventListener(OPEN_SEARCH_EVENT, onEvt);
    return () => window.removeEventListener(OPEN_SEARCH_EVENT, onEvt);
  }, [openPalette]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open) closePalette();
        else openPalette();
        return;
      }
      if (!open) return;
      if (e.key === "Escape") closePalette();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((s) => (s + 1) % Math.max(1, results.length));
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((s) => (s - 1 + results.length) % Math.max(1, results.length));
      }
      if (e.key === "Enter" && results[selected]) {
        e.preventDefault();
        const href = results[selected].href;
        track("global_search", { query: q, href, is_stock: href.startsWith("/stock/") });
        closePalette();
        router.push(href);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, closePalette, openPalette, results, selected, router]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center pt-[15vh] px-4 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Search"
      onClick={closePalette}
    >
      <div
        className="terminal-card w-full max-w-lg overflow-hidden shadow-[0_0_20px_rgba(0,255,255,0.12)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-cyan-500/20">
          <Search size={18} className="text-cyan-400 shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            placeholder="Search a stock (e.g. RELIANCE) or a page…"
            className="flex-1 bg-transparent text-slate-100 placeholder:text-gray-500 outline-none text-sm"
            autoFocus
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
          />
          <kbd className="text-[10px] text-gray-500 border border-gray-600 rounded px-1.5">ESC</kbd>
        </div>
        <div className="max-h-[60vh] overflow-y-auto py-1">
          {results.length === 0 ? (
            <div className="px-4 py-6 text-center text-gray-500 text-sm">No matches</div>
          ) : (
            results.map((r, i) => {
              const Icon = r.icon;
              return (
                <button
                  key={`${r.href}-${i}`}
                  type="button"
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors ${
                    i === selected
                      ? "bg-cyan-500/10 text-cyan-300 border-l-2 border-cyan-400"
                      : "text-slate-300 hover:bg-slate-800/50"
                  }`}
                  onMouseEnter={() => setSelected(i)}
                  onClick={() => {
                    track("global_search", { query: q, href: r.href, is_stock: r.href.startsWith("/stock/") });
                    closePalette();
                    router.push(r.href);
                  }}
                >
                  <Icon size={16} className="shrink-0 text-cyan-400/80" />
                  <span className="flex-1">{r.label}</span>
                  {r.hint && <span className="text-[10px] uppercase tracking-wide text-gray-500">{r.hint}</span>}
                </button>
              );
            })
          )}
        </div>
        <div className="px-4 py-2 border-t border-cyan-500/10 text-[10px] text-gray-500 flex justify-between">
          <span>↑↓ navigate</span>
          <span>Enter open</span>
        </div>
      </div>
    </div>
  );
}
