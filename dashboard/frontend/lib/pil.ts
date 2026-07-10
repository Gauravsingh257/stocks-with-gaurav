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

/** Auth header from the stored login token — PIL data is private (login-only). */
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = typeof window !== "undefined" ? localStorage.getItem("swg-auth-token") : null;
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(extra ?? {}) };
}

export async function pilFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers: authHeaders() });
  if (res.status === 401) throw new Error("Please sign in to view Portfolio Intelligence.");
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

export interface AttrGroup { name: string; n: number; hit_rate: number; avg_pnl_pct: number; total_pnl_pct: number; }
export interface Scorecard {
  book: string; scope: string; period: string;
  funnel: { closed: number; active: number; pending: number; expired: number; triggered_lifetime: number; accepted_lifetime: number };
  performance: { closed_trades: number; hit_rate_pct: number; expectancy: number; expectancy_pct: number; profit_factor: number; avg_hold_days: number; realized_pnl: number };
  attribution: {
    best_sector: AttrGroup | null; worst_sector: AttrGroup | null;
    best_entry_model: AttrGroup | null; worst_entry_model: AttrGroup | null;
    best_regime: AttrGroup | null; worst_regime: AttrGroup | null;
  };
  notable: {
    top_winners: { symbol: string; pnl_pct: number; exit_reason: string }[];
    top_losers: { symbol: string; pnl_pct: number; exit_reason: string }[];
    largest_missed_opportunity: { symbol: string; potential_upside_pct: number } | null;
    largest_avoided_loss: { symbol: string; risk_avoided_pct: number } | null;
  };
  quality: { engine_quality_score: number; portfolio_quality_score: number; ranking_quality: number | null; replacement_efficiency: number | null };
  error?: string;
}

export async function fetchScorecards(scope: "daily" | "monthly", period?: string): Promise<{ scope: string; period: string; cards: Record<string, Scorecard> }> {
  const q = new URLSearchParams({ scope, refresh: "1" });
  if (period) q.set("period", period);
  return pilFetch(`/api/intelligence/scorecards?${q.toString()}`);
}

export interface WhatIfResult { weights: Record<string, number>; ann_return_pct: number; ann_vol_pct: number; sharpe: number; max_drawdown_pct: number; }
export interface Analytics {
  contribution: { rows: { book: string; pnl: number; contribution_pct: number; return_contribution_pct: number }[]; top_contributor: string | null };
  correlation: { engines: string[]; matrix: Record<string, Record<string, number | null>> };
  diversification: { weights: Record<string, number>; per_book_vol_pct: Record<string, number>; weighted_avg_vol_pct: number; combined_vol_pct: number; diversification_benefit_pct: number; diversification_ratio: number };
  optimal: { max_sharpe: WhatIfResult | null; min_vol: WhatIfResult | null; current: WhatIfResult };
  leaderboard: Record<string, string>;
  per_engine: Record<string, { total_return_pct: number; max_drawdown_pct: number; sharpe: number; expectancy_pct: number }>;
}

export interface AllocationRow { book: string; current_value: number; current_weight: number; target_weight: number; deviation: number; target_value: number; required_delta: number; action: string; }
export interface Allocation {
  total_value: number;
  targets: Record<string, number>;
  rows: AllocationRow[];
  rebalance_needed: boolean;
  cash_required_to_rebalance: number;
  max_drift: number;
  warnings: Warning[];
}

export interface BookHealth { book: string; sub_scores: Record<string, number>; overall: number; status: string; worst_factor: string; best_factor: string; }
export type HealthMap = Record<string, BookHealth> & { overall_status?: string; overall_score?: number };
export async function fetchHealth(): Promise<HealthMap> { return pilFetch("/api/intelligence/health"); }

export async function fetchReport(kind: "daily" | "monthly", period?: string): Promise<{ kind: string; period: string; payload: Record<string, unknown>; html?: string | null }> {
  const q = new URLSearchParams({ kind });
  if (period) q.set("period", period);
  return pilFetch(`/api/intelligence/reports?${q.toString()}`);
}

export async function generateReport(kind: "daily" | "monthly", period?: string): Promise<{ kind: string; period: string; payload: Record<string, unknown>; html?: string | null }> {
  const q = new URLSearchParams({ kind });
  if (period) q.set("period", period);
  const res = await fetch(`${API_BASE}/api/intelligence/reports/generate?${q.toString()}`, { method: "POST", headers: authHeaders() });
  if (!res.ok) throw new Error(`generate report → ${res.status}`);
  return res.json();
}

export const STATUS_COLOR: Record<string, string> = { GREEN: "#34d399", YELLOW: "#f59e0b", RED: "#f43f5e" };

export interface Alert { id: number; ts: string; book: string; type: string; severity: string; message: string; value: number | null; threshold: number | null; active: number; }
export async function fetchAlerts(activeOnly = true): Promise<{ alerts: Alert[] }> {
  return pilFetch(`/api/intelligence/alerts?active_only=${activeOnly}`);
}
export async function evaluateAlerts(): Promise<{ fired: unknown[]; cleared: unknown[]; active_count: number }> {
  const res = await fetch(`${API_BASE}/api/intelligence/alerts/evaluate`, { method: "POST", headers: authHeaders() });
  if (!res.ok) throw new Error(`evaluate alerts → ${res.status}`);
  return res.json();
}

export interface PilConfig { capital: Record<string, number>; combined_capital: number; allocation_targets: Record<string, number>; [k: string]: unknown; }
export async function fetchPilConfig(): Promise<PilConfig> { return pilFetch("/api/intelligence/config"); }

export async function fetchAnalytics(): Promise<Analytics> { return pilFetch("/api/intelligence/analytics"); }
export async function fetchAllocation(): Promise<Allocation> { return pilFetch("/api/intelligence/allocation"); }

export async function whatIf(weights: Record<string, number>): Promise<WhatIfResult> {
  const res = await fetch(`${API_BASE}/api/intelligence/analytics/what-if`, {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ weights }),
  });
  if (!res.ok) throw new Error(`what-if → ${res.status}`);
  return res.json();
}

export async function setAllocationTargets(weights: Record<string, number>): Promise<Allocation> {
  const res = await fetch(`${API_BASE}/api/intelligence/allocation/targets`, {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ weights }),
  });
  if (!res.ok) throw new Error(`set targets → ${res.status}`);
  return res.json();
}

export async function setBookCapital(capital: Record<string, number>): Promise<{ capital: Record<string, number>; applied: Record<string, number> }> {
  const res = await fetch(`${API_BASE}/api/intelligence/config/capital`, {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ capital }),
  });
  if (!res.ok) throw new Error(`set capital → ${res.status}`);
  return res.json();
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
