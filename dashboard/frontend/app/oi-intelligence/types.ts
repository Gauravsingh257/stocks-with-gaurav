/**
 * OI Intelligence — shared types & helper functions.
 */
import { TrendingUp, TrendingDown, Minus, ArrowUp, ArrowDown } from "lucide-react";
import { createElement } from "react";

/* ── Types ─────────────────────────────────────────────────── */
export interface StrikeHeatmapEntry {
  underlying: string;
  strike: number;
  ce_oi: number;
  pe_oi: number;
  ce_change: number;   // fraction: 0.12 = +12% OI change vs last scan
  pe_change: number;
  ce_status: string;
  pe_status: string;
  spot?: number;       // current underlying spot — used for ATM detection
  strike_pcr?: number; // pe_oi / ce_oi at this strike
}

export interface MonthlyTap {
  symbol: string;
  strike: number;
  opt_type: string;
  monthly_low: number;
  current_price: number;
  state: string;
  tap_price?: number;
  tap_time?: string;
  bounce_pct?: number;
  cooldown_until?: string;
}

export interface ShortCoveringSignal {
  tradingsymbol: string;
  underlying: string;
  strike: number;
  opt_type: string;
  spot: number;
  score: number;
  oi_drop_pct: number;
  price_rise_pct: number;
  signal_type: string;
  trade_action: string;
  signal_time?: string;
}

export interface UnderlyingSummary {
  pcr: number;
  pcr_trend: string;
  bull_score: number;
  bear_score: number;
  bias: string;
  sc_active: boolean;
  sc_count: number;
}

export interface PCRHistoryPoint {
  time: string;
  pcr: number;
}

export interface BiasHistoryPoint {
  time: string;
  bias: string;
  confidence: number;
  pcr: number;
}

export interface OISnapshot {
  overall_bias: string;
  confidence: number;
  high_conviction: boolean;
  pcr: number;
  pcr_trend: string;
  bull_score: number;
  bear_score: number;
  strike_heatmap: StrikeHeatmapEntry[];
  monthly_taps: MonthlyTap[];
  short_covering_signals: ShortCoveringSignal[];
  underlying_summaries: Record<string, UnderlyingSummary>;
  execution_quality?: ExecutionQuality;
  pcr_history: PCRHistoryPoint[];
  bias_history: BiasHistoryPoint[];
  timestamp: string;
  market_open: boolean;
  market_state?: MarketState;
}

export interface ExecutionQuality {
  date: string;
  total_trades_today: number;
  index_trades_today: number;
  oi_sc_trades_today: number;
  win_rate_today: number;
  net_r_today: number;
  avg_r_today: number;
  oi_sc_mfe_r_avg: number;
  oi_sc_mae_r_avg: number;
  top_signal_time?: string | null;
  top_signal_symbol?: string | null;
  last_oi_sc_exit_time?: string | null;
  last_oi_sc_outcome?: "TARGET_HIT" | "SL_HIT" | string | null;
  last_oi_sc_symbol?: string | null;
}

export interface MarketStateEvent {
  type: string;
  direction: string;
  weight: number;
  detail: string;
}

export interface MarketState {
  state: string;
  prev_state: string;
  confidence: number;
  events: MarketStateEvent[];
  bull_score: number;
  bear_score: number;
  net: number;
  last_update: string | null;
  transition_time: string | null;
}

/* ── Helpers ───────────────────────────────────────────────── */
export function biasColor(bias: string): string {
  const b = bias?.toUpperCase() || "";
  if (b.includes("BULL")) return "var(--success)";
  if (b.includes("BEAR")) return "var(--danger)";
  return "var(--warning)";
}

export function biasIcon(bias: string) {
  const b = bias?.toUpperCase() || "";
  if (b.includes("BULL")) return createElement(TrendingUp, { size: 16 });
  if (b.includes("BEAR")) return createElement(TrendingDown, { size: 16 });
  return createElement(Minus, { size: 16 });
}

export function trendArrow(trend: string) {
  const t = trend?.toUpperCase() || "";
  if (t === "RISING") return createElement(ArrowUp, { size: 12, style: { color: "var(--success)" } });
  if (t === "FALLING") return createElement(ArrowDown, { size: 12, style: { color: "var(--danger)" } });
  return createElement(Minus, { size: 10, style: { color: "var(--text-dim)" } });
}

export function fmt(n: number | undefined, d = 2): string {
  if (n === undefined || n === null) return "—";
  return Number(n).toFixed(d);
}

export function fmtOI(n: number): string {
  if (!n) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
  return n.toString();
}

export function timeAgo(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export function pcrZone(pcr: number): { label: string; color: string } {
  if (pcr >= 1.2) return { label: "BULLISH", color: "var(--success)" };
  if (pcr <= 0.7) return { label: "BEARISH", color: "var(--danger)" };
  return { label: "NEUTRAL", color: "var(--warning)" };
}
