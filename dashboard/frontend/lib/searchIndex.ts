/**
 * lib/searchIndex.ts — the shared, pure search core behind the global
 * Ctrl/⌘+K search (components/CommandPalette.tsx).
 *
 * Pure on purpose: no React, no fetch, no "@/" imports. That keeps the ranking
 * unit-testable under plain Node (lib/searchIndex.test.mjs) and reusable by any
 * search box on the site. Loading the stock list lives in lib/stockIndexLoader.ts.
 *
 * WHY (2026-09-14 search audit): the old palette offered an "Open stock" row for
 * any query shaped like a ticker, without checking the stock existed. Since
 * 2026-07-12, 11 of 24 stock searches (46%) opened a 404 — RELIANCCE, ICICI,
 * DATA, PINELAB — and typing a page name such as "watchlist" put a fake WATCHLIST
 * stock above the real page. Here every stock result comes from the published
 * NSE universe, so a result can never point at a page that isn't there.
 */

export interface UniverseRow {
  symbol?: string | null;
  company_name?: string | null;
  sector?: string | null;
}

export interface StockEntry {
  symbol: string; // NSE symbol as published, upper-case (e.g. "M&M")
  name: string; // company name as published ("" when unknown)
  sector: string; // "" when unknown
  rank: number; // 0 = most traded — the universe list arrives turnover-sorted
  symbolKey: string; // symbol with punctuation removed ("MM")
  nameKey: string; // normalised name ("MAHINDRA MAHINDRA LIMITED")
  nameCompact: string; // nameKey without spaces
  words: string[]; // significant words of the name (noise removed)
}

export interface PageEntry {
  href: string;
  label: string;
  section: string;
  keywords: string;
}

export type ResultKind = "stock" | "sector" | "page";

export interface SearchResult {
  kind: ResultKind;
  href: string;
  label: string;
  detail: string;
  score: number;
  /** True when the match needed typo tolerance — reported to analytics. */
  fuzzy: boolean;
}

export interface SearchGroup {
  kind: ResultKind;
  title: string;
  results: SearchResult[];
}

interface Match {
  score: number;
  fuzzy: boolean;
}

const LIMITS: Record<ResultKind, number> = { stock: 6, sector: 2, page: 4 };
const TITLES: Record<ResultKind, string> = { stock: "Stocks", sector: "Sectors", page: "Pages" };

// Words that carry no identity in a company name: "LIMITED" must not make every
// stock a match for "lim", and "ltd" on its own should find nothing.
const NAME_NOISE = new Set([
  "LIMITED", "LTD", "THE", "OF", "AND", "CO", "COMPANY", "CORPORATION", "CORP", "INC", "PVT", "PRIVATE",
]);

// Placeholder sectors are not something anyone searches for.
const SECTOR_PLACEHOLDERS = new Set(["", "UNASSIGNED", "UNKNOWN", "OTHER", "OTHERS"]);

const compact = (s: string) => s.replace(/[^A-Z0-9]/g, "");

/** Upper-case, drop exchange decorations and punctuation, collapse whitespace. */
export function normalizeQuery(raw: string): string {
  return String(raw ?? "")
    .toUpperCase()
    .replace(/^\s*NSE:/, "")
    .replace(/\.NS\b/g, "")
    .replace(/[^A-Z0-9 ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** How many typos a query of this length may carry. Short queries get none —
 *  a 3-letter query with a typo allowance matches half the market. */
export function typoBudget(length: number): number {
  if (length < 4) return 0;
  if (length < 7) return 1;
  return 2;
}

/**
 * Optimal-string-alignment distance (Levenshtein + adjacent transposition), with
 * an early exit: returns `max + 1` as soon as the answer must exceed `max`.
 */
export function editDistance(a: string, b: string, max: number): number {
  if (a === b) return 0;
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > max) return max + 1;
  let prevPrev: number[] = new Array(lb + 1).fill(0);
  let prev: number[] = Array.from({ length: lb + 1 }, (_, j) => j);
  let cur: number[] = new Array(lb + 1).fill(0);
  for (let i = 1; i <= la; i++) {
    cur[0] = i;
    let rowMin = i;
    for (let j = 1; j <= lb; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a.charCodeAt(i - 1) === b.charCodeAt(j - 2) && a.charCodeAt(i - 2) === b.charCodeAt(j - 1)) {
        v = Math.min(v, prevPrev[j - 2] + 1);
      }
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;
    const recycled = prevPrev;
    prevPrev = prev;
    prev = cur;
    cur = recycled;
  }
  return prev[lb];
}

/** Build the stock index from universe rows. Duplicates and blank symbols are dropped. */
export function buildStockIndex(rows: UniverseRow[]): StockEntry[] {
  const seen = new Set<string>();
  const out: StockEntry[] = [];
  for (const r of rows ?? []) {
    const symbol = String(r?.symbol ?? "").toUpperCase().replace(/^NSE:/, "").trim();
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    const name = String(r?.company_name ?? "").trim();
    const nameKey = normalizeQuery(name);
    out.push({
      symbol,
      name,
      sector: String(r?.sector ?? "").trim(),
      rank: out.length,
      symbolKey: compact(symbol),
      nameKey,
      nameCompact: compact(nameKey),
      words: nameKey.split(" ").filter((w) => w && !NAME_NOISE.has(w)),
    });
  }
  return out;
}

/**
 * Every page the site's navigation exposes (sidebar + section tabs). The admin-
 * only Product Health page is deliberately absent. `portfolioHref` mirrors the
 * sidebar, which points Portfolio at /intelligence only when PIL is enabled; if
 * two entries share a destination they are merged so a page never appears twice.
 */
export function buildSearchPages(portfolioHref: string): PageEntry[] {
  const pages: PageEntry[] = [
    { href: "/command", label: "Command Center", section: "Home", keywords: "home dashboard today regime overview" },
    { href: "/terminal", label: "Terminal", section: "Home", keywords: "signals live trades engine" },
    { href: "/research", label: "AI Research", section: "Research", keywords: "research ideas analyze discovery swing long term final trade ideas" },
    { href: "/screeners", label: "Screeners", section: "Research", keywords: "scanner scanners supertrend sector rotation" },
    { href: "/universe", label: "Stock Universe", section: "Research", keywords: "all stocks fundamentals pe roe debt sectors list" },
    { href: "/research/track-record", label: "Ideas Track Record", section: "Research", keywords: "published ideas outcomes hit rate history" },
    { href: "/watchlist", label: "Watchlist", section: "You", keywords: "alerts monitor saved" },
    { href: portfolioHref, label: "Portfolio", section: "Portfolio", keywords: "holdings positions pms book" },
    { href: "/analytics", label: "Track Record", section: "Portfolio", keywords: "performance returns equity curve win rate" },
    { href: "/journal", label: "Journal", section: "Portfolio", keywords: "closed trades history exits" },
    { href: "/oi-intelligence", label: "Live OI Radar", section: "Markets", keywords: "markets options open interest pcr max pain" },
    { href: "/market-intelligence", label: "Market Intel", section: "Markets", keywords: "markets macro breadth fx global" },
  ];
  const byHref = new Map<string, PageEntry>();
  for (const p of pages) {
    const existing = byHref.get(p.href);
    if (existing) existing.keywords = `${existing.keywords} ${p.label} ${p.keywords}`;
    else byHref.set(p.href, { ...p });
  }
  return [...byHref.values()];
}

/** Every query token is a prefix of some word. The last token may be a single
 *  character, because the user is still typing it. */
function wordsMatchAll(tokens: string[], words: string[]): boolean {
  return (
    tokens.length > 0 &&
    tokens.every((t, i) => (t.length >= 2 || i === tokens.length - 1) && words.some((w) => w.startsWith(t)))
  );
}

/** Total typos needed to match every token to some word (or that word's leading
 *  slice), or null when any token is out of its typo budget.
 *
 *  Typos are only considered for words sharing the token's first letter. People
 *  rarely mistype the first letter, and the gate skips nearly every distance
 *  computation — full-universe typo search stays cheap enough for low-end phones. */
function fuzzyWordCost(tokens: string[], words: string[]): number | null {
  let total = 0;
  for (const t of tokens) {
    const budget = typoBudget(t.length);
    let best = Number.POSITIVE_INFINITY;
    for (const w of words) {
      if (w.startsWith(t)) {
        best = 0;
        break;
      }
      if (budget === 0 || w.charCodeAt(0) !== t.charCodeAt(0)) continue;
      const whole = editDistance(t, w, budget);
      const lead = w.length > t.length ? editDistance(t, w.slice(0, t.length), budget) : budget + 1;
      best = Math.min(best, whole, lead);
    }
    if (best > budget) return null;
    total += best;
  }
  return total;
}

function scoreStock(s: StockEntry, q: string, qc: string, sig: string[]): Match | null {
  if (s.symbolKey === qc) return { score: 1000, fuzzy: false };
  // Every prefix match scores the same, so liquidity (rank) decides the order:
  // "hdfc" should lead with HDFCBANK, not the shorter HDFCAMC. A length penalty
  // here ranked stocks by symbol length rather than by what people look for.
  if (s.symbolKey.startsWith(qc)) return { score: 900, fuzzy: false };
  if (sig.length) {
    if (qc.length >= 2 && s.nameCompact.startsWith(qc)) return { score: 820, fuzzy: false };
    if (wordsMatchAll(sig, s.words)) return { score: 760 + (s.words[0]?.startsWith(sig[0]) ? 20 : 0), fuzzy: false };
  }
  if (qc.length >= 3 && s.symbolKey.includes(qc)) return { score: 650, fuzzy: false };
  if (sig.length && q.length >= 3 && s.nameKey.includes(q)) return { score: 600, fuzzy: false };
  const budget = typoBudget(qc.length);
  if (budget > 0) {
    const d = editDistance(qc, s.symbolKey, budget);
    if (d <= budget) return { score: 460 - 60 * d, fuzzy: true };
  }
  if (sig.length && sig.some((t) => t.length >= 4)) {
    const cost = fuzzyWordCost(sig, s.words);
    if (cost !== null && cost > 0) return { score: 400 - 40 * cost, fuzzy: true };
  }
  return null;
}

// The universe's sector labels are terse ("Finance", "Pharma", "IT"). People
// search with everyday words, so each label also answers to these. Keys are the
// normalised labels as published in `stock_universe`.
const SECTOR_ALIASES: Record<string, string> = {
  FINANCE: "BANK BANKS BANKING FINANCIAL FINANCIALS NBFC INSURANCE LENDING",
  PHARMA: "PHARMACEUTICAL PHARMACEUTICALS HEALTHCARE HEALTH HOSPITAL HOSPITALS DRUGS MEDICAL",
  IT: "TECHNOLOGY TECH SOFTWARE INFORMATION",
  AUTO: "AUTOMOBILE AUTOMOBILES AUTOMOTIVE VEHICLES CARS",
  METAL: "METALS STEEL MINING ALUMINIUM COPPER",
  REALTY: "REAL ESTATE PROPERTY HOUSING",
  INFRA: "INFRASTRUCTURE CONSTRUCTION ENGINEERING",
  FMCG: "CONSUMER GOODS STAPLES",
  ENERGY: "OIL GAS PETROLEUM REFINERY",
  POWER: "ELECTRICITY",
  TELECOM: "TELECOMMUNICATION TELECOMMUNICATIONS",
  TEXTILES: "TEXTILE APPAREL",
  CHEMICALS: "CHEMICAL SPECIALTY",
  MEDIA: "ENTERTAINMENT BROADCASTING",
  CEMENT: "BUILDING MATERIALS",
};

function scoreSector(key: string, q: string, tokens: string[]): Match | null {
  const words = key.split(" ").filter(Boolean);
  const aliases = (SECTOR_ALIASES[key] ?? "").split(" ").filter(Boolean);
  if (key === q) return { score: 780, fuzzy: false };
  if (q.length >= 2 && key.startsWith(q)) return { score: 720, fuzzy: false };
  const wholeTokens = tokens.every((t) => t.length >= 2);
  if (wholeTokens && wordsMatchAll(tokens, words)) return { score: 700, fuzzy: false };
  if (wholeTokens && aliases.length && wordsMatchAll(tokens, words.concat(aliases))) return { score: 690, fuzzy: false };
  if (q.length >= 3 && key.includes(q)) return { score: 640, fuzzy: false };
  if (tokens.some((t) => t.length >= 4)) {
    const cost = fuzzyWordCost(tokens, words.concat(aliases));
    if (cost !== null && cost > 0) return { score: 420 - 60 * cost, fuzzy: true };
  }
  return null;
}

function scorePage(p: PageEntry, q: string, tokens: string[]): Match | null {
  const labelKey = normalizeQuery(p.label);
  const labelWords = labelKey.split(" ").filter(Boolean);
  const allWords = labelWords.concat(normalizeQuery(p.keywords).split(" ").filter(Boolean));
  if (labelKey === q) return { score: 950, fuzzy: false };
  if (labelKey.startsWith(q)) return { score: 880, fuzzy: false };
  if (wordsMatchAll(tokens, labelWords)) return { score: 840, fuzzy: false };
  if (tokens.every((t) => t.length >= 2) && wordsMatchAll(tokens, allWords)) return { score: 560, fuzzy: false };
  if (q.length >= 3 && labelKey.includes(q)) return { score: 540, fuzzy: false };
  if (tokens.some((t) => t.length >= 4)) {
    const cost = fuzzyWordCost(tokens, allWords);
    if (cost !== null && cost > 0) return { score: 420 - 60 * cost, fuzzy: true };
  }
  return null;
}

const sectorCache = new WeakMap<StockEntry[], [string, number][]>();

function sectorCounts(stocks: StockEntry[]): [string, number][] {
  const cached = sectorCache.get(stocks);
  if (cached) return cached;
  const counts = new Map<string, number>();
  for (const s of stocks) {
    if (SECTOR_PLACEHOLDERS.has(s.sector.toUpperCase())) continue;
    counts.set(s.sector, (counts.get(s.sector) ?? 0) + 1);
  }
  const out = [...counts.entries()];
  sectorCache.set(stocks, out);
  return out;
}

function pageResult(p: PageEntry, score: number, fuzzy: boolean): SearchResult {
  return { kind: "page", href: p.href, label: p.label, detail: p.section, score, fuzzy };
}

/**
 * Search stocks, sectors and pages. Returns non-empty groups, ordered by their
 * best result, so an exact page name ("watchlist") leads and an exact symbol
 * ("reliance") leads. With `stocks === null` (list not loaded) only pages are
 * searched — a stock is never guessed.
 */
export function searchAll(query: string, stocks: StockEntry[] | null, pages: PageEntry[]): SearchGroup[] {
  const q = normalizeQuery(query);
  if (!q) {
    return pages.length
      ? [{ kind: "page", title: TITLES.page, results: pages.map((p) => pageResult(p, 0, false)) }]
      : [];
  }
  const qc = compact(q);
  const tokens = q.split(" ").filter(Boolean);
  const sig = tokens.filter((t) => !NAME_NOISE.has(t));
  const groups: SearchGroup[] = [];

  if (stocks && stocks.length && qc) {
    const hits: { s: StockEntry; m: Match }[] = [];
    for (const s of stocks) {
      const m = scoreStock(s, q, qc, sig);
      if (m) hits.push({ s, m });
    }
    hits.sort((a, b) => b.m.score - a.m.score || a.s.rank - b.s.rank);
    const results = hits.slice(0, LIMITS.stock).map(
      ({ s, m }): SearchResult => ({
        kind: "stock",
        href: `/stock/${encodeURIComponent(s.symbol)}`,
        label: s.symbol,
        detail: [s.name, s.sector].filter(Boolean).join(" · "),
        score: m.score,
        fuzzy: m.fuzzy,
      }),
    );
    if (results.length) groups.push({ kind: "stock", title: TITLES.stock, results });

    const sectorHits: { sector: string; count: number; m: Match }[] = [];
    for (const [sector, count] of sectorCounts(stocks)) {
      const m = scoreSector(normalizeQuery(sector), q, tokens);
      if (m) sectorHits.push({ sector, count, m });
    }
    sectorHits.sort((a, b) => b.m.score - a.m.score || b.count - a.count);
    const sectorResults = sectorHits.slice(0, LIMITS.sector).map(
      ({ sector, count, m }): SearchResult => ({
        kind: "sector",
        href: `/universe?sector=${encodeURIComponent(sector)}`,
        label: sector,
        detail: `${count} stocks · Stock Universe`,
        score: m.score,
        fuzzy: m.fuzzy,
      }),
    );
    if (sectorResults.length) groups.push({ kind: "sector", title: TITLES.sector, results: sectorResults });
  }

  const pageHits: { p: PageEntry; m: Match }[] = [];
  for (const p of pages) {
    const m = scorePage(p, q, tokens);
    if (m) pageHits.push({ p, m });
  }
  pageHits.sort((a, b) => b.m.score - a.m.score);
  const pageResults = pageHits.slice(0, LIMITS.page).map(({ p, m }) => pageResult(p, m.score, m.fuzzy));
  if (pageResults.length) groups.push({ kind: "page", title: TITLES.page, results: pageResults });

  groups.sort((a, b) => b.results[0].score - a.results[0].score);
  return groups;
}

/** The flat, keyboard-navigable order of a grouped result set. */
export function flattenGroups(groups: SearchGroup[]): SearchResult[] {
  return groups.flatMap((g) => g.results);
}
