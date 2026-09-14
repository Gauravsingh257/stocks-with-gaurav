/**
 * lib/stockIndexLoader.ts — fetch-once, cache-locally loader for the global
 * search's stock list.
 *
 * Source: GET /api/research/universe/sitemap — the published NSE equity universe
 * as {symbol, company_name, sector}, turnover-sorted (~2,350 rows, ~37 KB
 * gzipped). It is a plain table read on the backend with no provider calls, and
 * the universe refreshes weekly, so a 12-hour browser cache is ample. The cache
 * stores compact tuples rather than the raw JSON to stay small in localStorage.
 *
 * Never throws. If the network fails, a stale cache beats nothing; with no cache
 * at all it resolves to null and the palette says so, rather than guessing at a
 * stock that may not exist.
 */
import { API_BASE } from "@/lib/api";
import { buildStockIndex, type StockEntry, type UniverseRow } from "@/lib/searchIndex";

const CACHE_KEY = "swg_stock_index_v1";
const TTL_MS = 12 * 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 12_000;

type Tuple = [string, string, string];
type Cached = { t: number; rows: Tuple[] };

let memo: StockEntry[] | null = null;
let inflight: Promise<StockEntry[] | null> | null = null;

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Cached;
    return parsed && typeof parsed.t === "number" && Array.isArray(parsed.rows) ? parsed : null;
  } catch {
    return null;
  }
}

function toIndex(rows: Tuple[]): StockEntry[] {
  return buildStockIndex(rows.map(([symbol, company_name, sector]) => ({ symbol, company_name, sector })));
}

/** The index if it is already in memory or freshly cached — synchronous, so a
 *  returning visitor's first keystroke is answered instantly. */
export function peekStockIndex(): StockEntry[] | null {
  if (memo) return memo;
  if (typeof window === "undefined") return null;
  const cached = readCache();
  if (cached && cached.rows.length && Date.now() - cached.t < TTL_MS) {
    memo = toIndex(cached.rows);
    return memo;
  }
  return null;
}

/** Load (or reuse) the stock index. Concurrent callers share one request. */
export function loadStockIndex(): Promise<StockEntry[] | null> {
  const ready = peekStockIndex();
  if (ready) return Promise.resolve(ready);
  if (inflight) return inflight;
  inflight = (async () => {
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS) : null;
    try {
      const res = await fetch(`${API_BASE}/api/research/universe/sitemap?limit=5000`, {
        signal: ctrl?.signal,
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as { items?: UniverseRow[] };
      const rows: Tuple[] = (body.items ?? [])
        .filter((r) => r && r.symbol)
        .map((r) => [String(r.symbol), String(r.company_name ?? ""), String(r.sector ?? "")]);
      if (!rows.length) throw new Error("empty universe");
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({ t: Date.now(), rows } satisfies Cached));
      } catch {
        /* storage full or disabled — the in-memory index still works */
      }
      memo = toIndex(rows);
      return memo;
    } catch {
      const stale = readCache();
      if (stale?.rows.length) {
        memo = toIndex(stale.rows);
        return memo;
      }
      return null;
    } finally {
      if (timer) clearTimeout(timer);
      inflight = null;
    }
  })();
  return inflight;
}
