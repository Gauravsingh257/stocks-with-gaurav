/**
 * lib/pil.ts
 * Client helpers for the Portfolio Intelligence Layer (PIL) API.
 * Read-only: PIL never influences an engine; the UI only observes.
 */
import { API_BASE } from "@/lib/api";

export const PIL_BOOKS = ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"] as const;
export type Book = (typeof PIL_BOOKS)[number];

export const BOOK_LABEL: Record<string, string> = {
  SWING: "Swing",
  LONGTERM: "Long-Term",
  MOMENTUM: "Momentum",
  COMBINED: "Combined",
};

export interface BookMetrics {
  book: string;
  label: string;
  portfolio_value: number;
  invested_capital: number;
  available_cash: number;
  initial_capital: number;
  realized_pnl: number;
  unrealized_pnl: number;
  open_positions: number;
  pending_positions: number;
  total_return_pct: number;
  today_return_pct: number;
  mtd_pct: number;
  qtd_pct: number;
  ytd_pct: number;
  cagr_pct: number;
  volatility_pct: number;
  max_drawdown_pct: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  risk_score: number;
  closed_trades: number;
  hit_rate_pct: number;
  expectancy: number;
  expectancy_pct: number;
  profit_factor: number;
  avg_winner: number;
  avg_loser: number;
  win_loss_ratio: number;
  avg_hold_days: number;
  turnover_pct: number;
}

export interface EquityPoint { date: string; value: number; }

export async function pilFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`PIL ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function fetchComparison(): Promise<{ metrics: Record<string, BookMetrics>; order: string[] }> {
  return pilFetch("/api/intelligence/comparison");
}

export interface Bucket { name: string; value: number; pct: number; count: number; books: string[]; }
export interface Holding { symbol: string; value: number; pct: number; pct_nav: number; sector: string; books: string[]; }
export interface Warning { type: string; severity: string; message: string; value: number; threshold: number; }

export interface Exposure {
  nav: number;
  deployed: number;
  cash_pct: number;
  by_sector: Bucket[];
  by_industry: Bucket[];
  by_market_cap: Bucket[];
  by_theme: Bucket[];
  by_book: Bucket[];
  holdings: Holding[];
  top10: Holding[];
  top10_pct: number;
  largest_holding: Holding | null;
  hhi: number;
  effective_holdings: number;
  diversification_score: number;
  portfolio_beta: number;
  liquidity_coverage_pct: number;
  heatmap: Record<string, number | string>[];
  correlation: { engines: string[]; matrix: Record<string, Record<string, number | null>> };
  warnings: Warning[];
}

export async function fetchExposure(): Promise<Exposure> {
  return pilFetch("/api/intelligence/exposure");
}

export async function fetchCombined(): Promise<{
  books: Record<string, { ledger: Record<string, unknown>; equity_curve: EquityPoint[]; metrics: BookMetrics }>;
  order: string[];
}> {
  return pilFetch("/api/intelligence/combined");
}

// ── formatters (Indian numbering) ────────────────────────────────────────────

export function fmtINR(v: number | null | undefined, compact = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (compact && abs >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`;
  if (compact && abs >= 1e5) return `₹${(v / 1e5).toFixed(2)}L`;
  if (compact && abs >= 1e3) return `₹${(v / 1e3).toFixed(1)}K`;
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function toneClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return "text-[var(--text-secondary)]";
  return v > 0 ? "text-emerald-400" : "text-rose-400";
}

/** Consistent per-book accent color for charts/legends. */
export const BOOK_COLOR: Record<string, string> = {
  SWING: "#22d3ee",     // cyan
  LONGTERM: "#a78bfa",  // violet
  MOMENTUM: "#f59e0b",  // amber
  COMBINED: "#34d399",  // emerald
};
