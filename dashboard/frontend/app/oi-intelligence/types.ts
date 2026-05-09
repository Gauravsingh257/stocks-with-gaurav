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

export interface OIInterpretEvent {
  type: string;
  underlying: string;
  strike: number | null;
  confidence?: number;
  text: string;
}

export interface OIStrikeNarrative {
  underlying: string;
  strike: number;
  headline: string;
  detail: string;
  tone?: string;
}

export interface OIGuidanceUnderlying {
  bullish_above?: number | null;
  bearish_below?: number | null;
  avoid_trade_zone?: number[] | null;
  trap_zone?: number[] | null;
  breakout_trigger?: number | null;
  momentum_expansion_above?: number | null;
  weak_structure_below?: number | null;
  liquidity_sweep_risk?: "low" | "moderate" | "elevated" | string;
  avoid_aggressive_shorts_above?: number | null;
  high_volatility_zone?: boolean;
  structure_note?: string | null;
}

export interface OIMarketRegime {
  code?: string;
  label?: string;
  confidence?: number;
  drivers?: string[];
}

export interface OIWhatChanged {
  window_minutes?: number;
  window_label?: string;
  bullets?: { text: string; tone?: string }[];
  immediate_vs_prior?: { text: string; tone?: string }[];
  rolling_window?: { text: string; tone?: string }[];
  persistence_note?: string | null;
}

export interface OIInstitutionalTag {
  id?: string;
  label?: string;
  likelihood?: number;
  note?: string;
}

export interface OIShiftEvent {
  type?: string;
  underlying?: string;
  strike?: number | null;
  confidence?: number;
  text?: string;
}

export interface OIStrikeIntelCard {
  underlying?: string;
  strike?: number;
  role?: string | null;
  headline?: string;
  detail?: string;
  shift?: string | null;
  tone?: string;
  intensity?: number;
}

export interface OIFlowStrength {
  pressure_strength?: string;
  bullish_pressure_trend?: string;
  flow_acceleration?: number;
  flow_persistence?: number;
  flow_decay?: number;
  pressure_strength_score?: number;
  scores?: { acceleration?: number; persistence?: number; decay_risk?: number };
  narratives?: string[];
}

export interface OIStrikeEvolutionRow {
  underlying?: string;
  strike?: number | null;
  evolution?: string;
  arrow?: string;
  label?: string;
  short?: string;
  strength_delta?: string;
}

export interface OIInstitutionalIntentV2 {
  intents?: {
    id?: string;
    label?: string;
    probability_pct?: number;
    confidence_cap?: number;
    note?: string;
  }[];
  disclaimer?: string;
}

export interface OISmartTradingActions {
  preferred_strategy_lines?: string[];
  avoid_lines?: string[];
  risk_context_lines?: string[];
  breakout_quality?: string;
  momentum_quality?: string;
  trap_probability_hint?: string;
}

export interface OICrossSessionCompare {
  available?: boolean;
  note?: string;
  vs_session_date?: string;
  bullets?: string[];
  compared_at?: string;
}

export interface OIStoryTimelineEvent {
  ts?: string;
  ts_ist?: string;
  regime?: string;
  dominant_shift?: string | null;
  support_migration?: string | null;
  resistance_migration?: string | null;
  institutional_hint?: string;
  confidence_bull?: number | null;
  headline?: string;
}

export interface OISessionEvolution {
  cross_session?: OICrossSessionCompare;
  story_timeline?: OIStoryTimelineEvent[];
  session_date_ist?: string;
}

export interface OIInterpretationDigest {
  regime?: string | null;
  regime_label?: string | null;
  bias?: string | null;
  top_story?: string;
  strongest_shift?: string;
  strongest_shift_text?: string;
  top_guidance_underlying?: string | null;
  bullish_above?: number | null;
  bearish_below?: number | null;
  confidence_bullish_pct?: number | null;
  confidence_bearish_pct?: number | null;
  engine_version?: string;
  generated_at?: string;
}

export interface OIPhase15Meta {
  generation_latency_ms?: number;
  digest_bytes_estimate?: number;
  timeline_events_loaded?: number;
  ws_full_interpretation_bytes_estimate?: number;
}

export interface OIInterpretation {
  summary_lines: string[];
  events: OIInterpretEvent[];
  strike_narratives: OIStrikeNarrative[];
  guidance: Record<string, OIGuidanceUnderlying>;
  delta_vs_prior: {
    has_prior?: boolean;
    pcr_now?: number;
    pcr_momentum?: string;
    bias_snapshot?: string;
    dominant_strike_stories?: OIStrikeNarrative[];
    rolling_anchor_age_sec?: number | null;
    error?: string;
  };
  engine_version?: string;
  generated_at?: string;
  market_regime?: OIMarketRegime;
  market_story?: { headline?: string; paragraphs?: string[] };
  what_changed?: OIWhatChanged;
  institutional_positioning?: {
    summary?: string;
    tags?: OIInstitutionalTag[];
  };
  confidence?: {
    bullish_pct?: number;
    bearish_pct?: number;
    regime_pct?: number;
  };
  oi_shifts?: OIShiftEvent[];
  strike_intelligence?: OIStrikeIntelCard[];
  flow_strength?: OIFlowStrength;
  strike_evolution?: OIStrikeEvolutionRow[];
  institutional_intent_v2?: OIInstitutionalIntentV2;
  smart_trading_actions?: OISmartTradingActions;
  session_evolution?: OISessionEvolution;
  interpretation_digest?: OIInterpretationDigest;
  phase15_meta?: OIPhase15Meta;
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
  market_hours?: boolean;    // from backend meta — true during 09:15-15:31 IST Mon-Fri
  last_update?: string;      // ISO timestamp of last snapshot generation
  oi_sentiment_update?: string | null;
  market_state?: MarketState;
  interpretation?: OIInterpretation;
  /** Shallow copy of interpretation_digest for fast WS / mobile clients */
  interpretation_digest?: OIInterpretationDigest;
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
