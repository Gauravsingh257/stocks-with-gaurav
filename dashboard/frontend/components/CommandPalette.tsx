"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BarChart2, BookOpen, Bookmark, Bot, Database, Eye, Globe, History, Home, Layers,
  LayoutDashboard, Loader, Radar, Search, Sparkles, TrendingUp, X,
} from "lucide-react";
import { track } from "@/lib/analytics";
import {
  buildSearchPages, flattenGroups, normalizeQuery, searchAll,
  type ResultKind, type SearchResult, type StockEntry,
} from "@/lib/searchIndex";
import { loadStockIndex, peekStockIndex } from "@/lib/stockIndexLoader";

/**
 * Global search — Ctrl/⌘+K anywhere, or the top-bar pill (components/SearchPill).
 *
 * Phase 1 (2026-09-14): validated stock + company-name autocomplete with typo
 * tolerance, sectors, and every navigable page. Ranking lives in
 * lib/searchIndex.ts (pure, unit-tested); the stock list in lib/stockIndexLoader.ts.
 * A stock is only ever offered if it exists in the published universe — the old
 * palette sent 11 of 24 stock searches to a 404.
 *
 * Analytics (lib/analytics → first-party store + GA4/Clarity/PostHog):
 *   search_opened      { trigger }
 *   global_search      a result was chosen: { query, href, is_stock, result_type,
 *                      position (1-based), action, input, fuzzy, trigger, index }.
 *                      Same event name Product Health already counts as "Global
 *                      Search", so that history stays continuous.
 *   search_no_results  { query, query_len, index } — once per distinct query,
 *                      after typing pauses.
 */

export const OPEN_SEARCH_EVENT = "swg:open-search";

/** Open the palette from anywhere; `trigger` is reported to analytics. */
export function openGlobalSearch(trigger: string = "unknown") {
  if (typeof window === "undefined") return;
  const how = typeof trigger === "string" ? trigger : "unknown";
  window.dispatchEvent(new CustomEvent(OPEN_SEARCH_EVENT, { detail: { trigger: how } }));
}

/** Start loading the stock list early (e.g. on hover) so the first keystroke is instant. */
export function prewarmSearchIndex() {
  void loadStockIndex();
}

// Mirrors components/Sidebar.tsx: Portfolio is /intelligence unless PIL is off.
const PIL_ENABLED = process.env.NEXT_PUBLIC_PIL_ENABLED !== "0";
const PAGES = buildSearchPages(PIL_ENABLED ? "/intelligence" : "/analytics");

const PAGE_ICONS: Record<string, typeof BarChart2> = {
  "/command": Home,
  "/terminal": Sparkles,
  "/research": Bot,
  "/screeners": Radar,
  "/universe": Database,
  "/research/track-record": History,
  "/watchlist": Bookmark,
  "/intelligence": LayoutDashboard,
  "/analytics": BarChart2,
  "/journal": BookOpen,
  "/oi-intelligence": Eye,
  "/market-intelligence": Globe,
};

const KIND_HINT: Record<ResultKind, string> = { stock: "Open stock", sector: "Filter universe", page: "" };
const ZERO_RESULT_DELAY_MS = 900;
const MAX_QUERY_LOGGED = 40;

function iconFor(r: SearchResult): typeof BarChart2 {
  if (r.kind === "stock") return TrendingUp;
  if (r.kind === "sector") return Layers;
  return PAGE_ICONS[r.href] ?? Search;
}

type Input = "keyboard" | "pointer";

export default function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [stocks, setStocks] = useState<StockEntry[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const triggerRef = useRef("unknown");
  const reportedZeroRef = useRef("");

  const groups = useMemo(() => searchAll(query, stocks, PAGES), [query, stocks]);
  const results = useMemo(() => flattenGroups(groups), [groups]);
  // Index of each group's first row in the flat keyboard order (≤3 groups).
  const offsets = useMemo(
    () => groups.map((_, gi) => groups.slice(0, gi).reduce((sum, g) => sum + g.results.length, 0)),
    [groups],
  );
  const active = results.length ? Math.min(selected, results.length - 1) : -1;
  const normalized = normalizeQuery(query);
  const indexState = stocks ? "ready" : loadFailed ? "unavailable" : "loading";
  const loggedQuery = query.trim().slice(0, MAX_QUERY_LOGGED);

  const closePalette = useCallback(() => {
    setOpen(false);
    setQuery("");
    setSelected(0);
  }, []);

  const openPalette = useCallback((trigger: string) => {
    triggerRef.current = trigger;
    reportedZeroRef.current = "";
    setQuery("");
    setSelected(0);
    setLoadFailed(false);
    const ready = peekStockIndex();
    if (ready) setStocks(ready);
    setOpen(true);
    track("search_opened", { trigger });
  }, []);

  // Plain client navigation everywhere. /universe reads ?sector= / ?q= through
  // useSearchParams and keys its table on them, so a sector link applies its
  // filter even when followed from the Universe page itself.
  const navigate = useCallback(
    (href: string) => {
      closePalette();
      router.push(href);
    },
    [closePalette, router],
  );

  const choose = useCallback(
    (r: SearchResult, index: number, input: Input) => {
      const action = r.kind === "stock" ? "open_stock" : r.kind === "sector" ? "open_sector" : "open_page";
      track("global_search", {
        query: loggedQuery,
        href: r.href,
        is_stock: r.kind === "stock",
        result_type: r.kind,
        position: index + 1,
        action,
        input,
        fuzzy: r.fuzzy,
        trigger: triggerRef.current,
        index: indexState,
      });
      navigate(r.href);
    },
    [loggedQuery, indexState, navigate],
  );

  const browseUniverse = useCallback(
    (input: Input) => {
      const href = `/universe?q=${encodeURIComponent(query.trim())}`;
      track("global_search", {
        query: loggedQuery,
        href,
        is_stock: false,
        result_type: "fallback",
        position: 0,
        action: "browse_universe",
        input,
        fuzzy: false,
        trigger: triggerRef.current,
        index: indexState,
      });
      navigate(href);
    },
    [query, loggedQuery, indexState, navigate],
  );

  // Open triggers: the custom event (SearchPill) …
  useEffect(() => {
    const onEvt = (e: Event) => {
      const detail = (e as CustomEvent<{ trigger?: string }>).detail;
      openPalette(detail?.trigger ?? "unknown");
    };
    window.addEventListener(OPEN_SEARCH_EVENT, onEvt);
    return () => window.removeEventListener(OPEN_SEARCH_EVENT, onEvt);
  }, [openPalette]);

  // … load the stock list when opened (instant if already cached) …
  useEffect(() => {
    if (!open || stocks) return;
    let alive = true;
    loadStockIndex().then((idx) => {
      if (!alive) return;
      if (idx) setStocks(idx);
      else setLoadFailed(true);
    });
    return () => {
      alive = false;
    };
  }, [open, stocks]);

  // … and the keyboard: Ctrl/⌘+K toggles; arrows, Enter and Esc inside.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open) closePalette();
        else openPalette("shortcut");
        return;
      }
      if (!open) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closePalette();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        if (results.length) setSelected((active + 1) % results.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (results.length) setSelected((active - 1 + results.length) % results.length);
      } else if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        if (active >= 0) choose(results[active], active, "keyboard");
        else if (normalized.length >= 2 && indexState !== "loading") browseUniverse("keyboard");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, results, active, normalized, indexState, choose, browseUniverse, closePalette, openPalette]);

  // Report a zero-result search once typing pauses — never per keystroke.
  useEffect(() => {
    if (!open || normalized.length < 2 || results.length > 0 || indexState === "loading") return;
    if (reportedZeroRef.current === normalized) return;
    const id = setTimeout(() => {
      reportedZeroRef.current = normalized;
      track("search_no_results", { query: loggedQuery, query_len: normalized.length, index: indexState });
    }, ZERO_RESULT_DELAY_MS);
    return () => clearTimeout(id);
  }, [open, normalized, results.length, indexState, loggedQuery]);

  // Keep the keyboard-selected row visible.
  useEffect(() => {
    if (!open || active < 0) return;
    document.getElementById(`swg-search-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  if (!open) return null;

  const noMatch = normalized.length > 0 && results.length === 0 && indexState !== "loading";

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center px-3 pt-3 sm:pt-[12vh] bg-black/70 backdrop-blur-sm"
      role="presentation"
      onClick={closePalette}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search stocks, sectors and pages"
        className="terminal-card w-full max-w-xl overflow-hidden flex flex-col shadow-[0_0_20px_rgba(0,255,255,0.12)]"
        style={{ maxHeight: "calc(100dvh - 24px)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-cyan-500/20">
          <Search size={18} className="text-cyan-400 shrink-0" aria-hidden />
          <input
            type="search"
            role="combobox"
            aria-expanded={results.length > 0}
            aria-controls="swg-search-list"
            aria-autocomplete="list"
            aria-activedescendant={active >= 0 ? `swg-search-opt-${active}` : undefined}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            placeholder="Search stocks, sectors, pages…"
            inputMode="search"
            enterKeyHint="go"
            className="flex-1 min-w-0 bg-transparent text-slate-100 placeholder:text-gray-500 outline-none text-base sm:text-sm"
            autoFocus
            autoCapitalize="off"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
          />
          {indexState === "loading" && normalized && (
            <Loader size={14} className="animate-spin text-gray-500 shrink-0" aria-label="Loading stock list" />
          )}
          <kbd className="hidden sm:inline text-[10px] text-gray-500 border border-gray-600 rounded px-1.5">ESC</kbd>
          <button
            type="button"
            onClick={closePalette}
            aria-label="Close search"
            className="sm:hidden grid place-items-center w-10 h-10 -mr-2 text-gray-400"
          >
            <X size={18} />
          </button>
        </div>

        <div id="swg-search-list" role="listbox" aria-label="Search results" className="overflow-y-auto min-h-0 py-1">
          {!normalized && (
            <div className="px-4 pt-2 pb-1 text-[11px] text-gray-500">
              Try a company, symbol or sector — e.g. Tata Motors, HDFCBANK, Pharma
            </div>
          )}
          {loadFailed && (
            <div className="px-4 py-2 text-[11px] text-amber-400/80">
              Stock list unavailable right now — showing pages only.
            </div>
          )}
          {indexState === "loading" && normalized && results.length === 0 && (
            <div className="px-4 py-6 text-center text-gray-500 text-sm">Loading stocks…</div>
          )}
          {noMatch && (
            <div className="px-4 py-6 text-center text-sm">
              <div className="text-gray-400">No stock, sector or page matches “{query.trim()}”.</div>
              <div className="text-gray-500 text-xs mt-1">Check the spelling, or try the company name.</div>
              {normalized.length >= 2 && (
                <button
                  type="button"
                  onClick={() => browseUniverse("pointer")}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-md px-3 min-h-[40px] text-xs text-cyan-300 border border-cyan-500/30 hover:bg-cyan-500/10"
                >
                  <Database size={13} /> Browse the Stock Universe
                </button>
              )}
            </div>
          )}

          {groups.map((g, gi) => (
            <div key={g.kind} role="group" aria-label={g.title}>
              <div className="px-4 pt-2.5 pb-1 text-[10px] uppercase tracking-wider text-gray-500">
                {normalized ? g.title : "Go to"}
              </div>
              {g.results.map((r, ri) => {
                const i = offsets[gi] + ri;
                const Icon = iconFor(r);
                const isActive = i === active;
                return (
                  <button
                    key={`${r.kind}:${r.href}`}
                    id={`swg-search-opt-${i}`}
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    className={`w-full flex items-center gap-3 px-4 py-2 min-h-[44px] text-left text-sm transition-colors border-l-2 ${
                      isActive
                        ? "bg-cyan-500/10 text-cyan-300 border-cyan-400"
                        : "text-slate-300 hover:bg-slate-800/50 border-transparent"
                    }`}
                    onMouseMove={() => {
                      if (!isActive) setSelected(i);
                    }}
                    onClick={() => choose(r, i, "pointer")}
                  >
                    <Icon size={16} className="shrink-0 text-cyan-400/80" aria-hidden />
                    <span className="flex-1 min-w-0">
                      <span className={`block truncate ${r.kind === "stock" ? "font-mono font-semibold" : ""}`}>
                        {r.label}
                      </span>
                      {r.kind !== "page" && r.detail && (
                        <span className="block truncate text-[11px] text-gray-500">
                          {r.fuzzy ? "Closest match · " : ""}
                          {r.detail}
                        </span>
                      )}
                    </span>
                    <span className="hidden sm:inline shrink-0 text-[10px] uppercase tracking-wide text-gray-500">
                      {r.kind === "page" ? r.detail : KIND_HINT[r.kind]}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        <div className="hidden sm:flex px-4 py-2 border-t border-cyan-500/10 text-[10px] text-gray-500 justify-between">
          <span>↑↓ navigate · Enter open · Esc close</span>
          <span>{stocks ? `${stocks.length.toLocaleString("en-IN")} NSE stocks` : ""}</span>
        </div>
      </div>
    </div>
  );
}
