/**
 * lib/api.ts
 * Typed API client for FastAPI backend.
 * All functions return typed data or throw on error.
 *
 * Backend URL: NEXT_PUBLIC_BACKEND_URL || BACKEND_URL (rewrites use BACKEND_URL at build).
 * If neither is set, /api/* goes through Next.js rewrites to BACKEND_URL (build-time).
 */
export function getBackendBase(): string {
  const backend =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.BACKEND_URL ||
    "";
  const base = (typeof backend === "string" && backend) ? backend.replace(/\/$/, "") : "";
  if (typeof window !== "undefined" && !base) {
    if (!(window as unknown as { __kite_backend_warned?: boolean }).__kite_backend_warned) {
      (window as unknown as { __kite_backend_warned?: boolean }).__kite_backend_warned = true;
      console.error(
        "[API] Backend URL not configured. Set NEXT_PUBLIC_BACKEND_URL (and BACKEND_URL for rewrites) in Vercel — required for /api/* and WebSocket."
      );
    }
  }
  return base;
}

/** Same as backend: use NEXT_PUBLIC_BACKEND_URL so Engine ON/OFF polling hits your Railway API.
 *  NOTE: Falls back to "" (empty) in production so Next.js rewrites handle routing.
 *  NEVER fall back to localhost in production — that causes 503s on Vercel.
 */
export const API_BASE = getBackendBase();

const BASE = getBackendBase();

const REQUEST_TIMEOUT_MS = 15_000;

function parseRetryAfterSeconds(res: Response, detail?: unknown): number {
  const header = Number(res.headers.get("Retry-After") || "");
  if (Number.isFinite(header) && header > 0) return Math.floor(header);
  if (detail && typeof detail === "object") {
    const value = Number((detail as { retry_after_seconds?: unknown }).retry_after_seconds ?? 0);
    if (Number.isFinite(value) && value > 0) return Math.floor(value);
  }
  return 1;
}

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithTimeout(path: string, init: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${BASE}${path}`, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`API ${path} timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function requestJson<T>(path: string, init: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetchWithTimeout(path, init, timeoutMs);

    if (res.ok) {
      return res.json() as Promise<T>;
    }

    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      detail = null;
    }

    if (res.status === 429 && attempt === 0) {
      const retryAfterSec = Math.min(parseRetryAfterSeconds(res, detail), 5);
      await delay(retryAfterSec * 1000);
      continue;
    }

    const detailText =
      detail && typeof detail === "object" && "detail" in detail && typeof (detail as { detail?: unknown }).detail === "string"
        ? `: ${(detail as { detail: string }).detail}`
        : "";
    lastError = new Error(`API ${path} → ${res.status}${detailText}`);
    break;
  }

  throw lastError ?? new Error(`API ${path} failed`);
}

async function get<T>(path: string, authToken?: string | null, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const init: RequestInit = { cache: "no-store" };
  if (authToken) init.headers = { Authorization: `Bearer ${authToken}` };
  return requestJson<T>(path, init, timeoutMs);
}

async function post<T>(path: string, body?: Record<string, unknown>, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const opts: RequestInit = { method: "POST" };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  return requestJson<T>(path, opts, timeoutMs);
}

// ── Types ────────────────────────────────────────────────────────────────────

export interface EngineSnapshot {
  active_trades:       ActiveTrade[];
  active_trade_count:  number;
  zone_state:          Record<string, ZoneEntry>;
  daily_pnl_r:         number;
  consecutive_losses:  number;
  signals_today:       number;
  traded_today:        string[];
  circuit_breaker_active: boolean;
  market_regime:       "BULLISH" | "BEARISH" | "NEUTRAL";
  max_daily_loss_r:    number;
  max_daily_signals:   number;
  engine_mode:         string;
  active_strategies:   Record<string, boolean>;
  index_only:          boolean;
  paper_mode:          boolean;
  engine_live:         boolean;
  engine_running?:     boolean;
  engine_heartbeat_age_sec?: number | null;
  engine_last_cycle_age_sec?: number | null;
  engine_started_at?:  number | null;
  snapshot_time:       string;
  /** Unified orchestration version from Redis (additive envelope). */
  _global_state_version?: number;
  _snapshot_ts?:       string;
  _snapshot_age_ms?:   number | null;
  _snapshot_origin?:   string;
  stale?:              boolean;
  data_source?:        string;
  setup_d_state?:      Record<string, SetupDEntry>;
  adaptive_intel?:     AdaptiveIntel;
  /** Index LTP from cache (NIFTY 50, NIFTY BANK) for command bar / sparklines */
  index_ltp?:          Record<string, number>;
  /** Equity symbols (uppercase, e.g. RELIANCE) from Redis ltp:{SYM} for unified realtime desk */
  equity_ltp?:         Record<string, number>;
}

export interface AdaptiveEvent {
  ts: string;
  symbol: string;
  setup: string;
  direction: "LONG" | "SHORT" | string;
  reason?: string;
  ai_score?: number;
}

export interface AdaptiveIntel {
  setup_multipliers: Record<string, number>;
  recent_blocks: AdaptiveEvent[];
  recent_ai_scores: AdaptiveEvent[];
}

export interface SetupDEntry {
  bias?:          string;
  stage?:         string;
  is_gap_day?:    boolean;
  choch_level?:   number;
  choch_time?:    string;
  bos_confirmed?: boolean;
  sweep_detected?: boolean;
}

export interface ActiveTrade {
  symbol:    string;
  setup:     string;
  direction: "LONG" | "SHORT";
  entry:     number;
  sl:        number;
  target:    number;
  rr:        number;
  start_time?: string;
}

export interface ZoneEntry {
  LONG?:  ZoneState | null;
  SHORT?: ZoneState | null;
}

export interface ZoneState {
  zone:  [number, number];
  state: "ACTIVE" | "TAPPED";
  tf:    string;
}

export interface DailyPnL {
  daily_pnl_r:         number;
  consecutive_losses:  number;
  circuit_breaker_active: boolean;
  signals_today:       number;
  max_daily_signals:   number;
  pnl_status:          "NORMAL" | "WARNING" | "CRITICAL";
}

export interface AnalyticsSummary {
  total_trades:       number;
  win_rate:           number;
  profit_factor:      number;
  expectancy_r:       number;
  total_r:            number;
  max_drawdown_r:     number;
  max_consec_losses:  number;
}

export interface EquityPoint { date: string; cumulative_r: number; }
export interface SetupStat {
  setup:        string;
  total:        number;
  wins:         number;
  win_rate:     number;
  total_r:      number;
  expectancy_r: number;
}
export interface RollingWRPoint { idx: number; date: string; win_rate: number; }
export interface CalendarDay { date: string; pnl_r: number; count: number; }
export interface DrawdownEvent { start: string; end: string; depth_r: number; bars: number; }

export interface JournalPage {
  trades:   JournalTrade[];
  total:    number;
  limit:    number;
  offset:   number;
  has_more: boolean;
}

export interface JournalTrade {
  id:          number;
  date:        string;
  symbol:      string;
  direction:   "LONG" | "SHORT";
  setup:       string;
  entry:       number;
  exit_price:  number | null;
  result:      "WIN" | "LOSS" | "RUNNING";
  pnl_r:       number;
  score:       number | null;
  notes:       string | null;
  signal_id?:  string | null;  // Phase 4A: link back to originating signal_log row
}

/** Row from ai_learning signal_log (Telegram + metadata); used by journal + analytics fallback. */
export interface SignalLogEntry {
  signal_id:         string;
  timestamp:         string | null;
  symbol:            string | null;
  direction:         string | null;
  strategy_name:     string | null;
  entry:             number | null;
  stop_loss:         number | null;
  target1:           number | null;
  target2:           number | null;
  score:             number | null;
  confidence:        number | null;
  result:            string | null;
  pnl_r:             number | null;
  created_at:        string;
  signal_kind?:      string | null;
  delivery_channel?: string | null;
  delivery_format?:  string | null;
  signal_json?:      string | null;
}

/** @deprecated Prefer SignalLogEntry — kept for older imports. */
export type SignalToday = SignalLogEntry;

export interface SignalLogPage {
  signals:    SignalLogEntry[];
  count:      number;
  total:      number;
  date_from:  string;
  date_to:    string;
  limit:      number;
  offset:     number;
  has_more:   boolean;
  source:     string;
}

export interface SmcEvidence {
  ob_zone: { low: number; high: number; tf: string } | null;
  fvg_range: { low: number; high: number; tf: string } | null;
  sweep_level: { price: number; side: "low" | "high" } | null;
  structure: "BOS" | "CHOCH" | "NONE";
  structure_dir: "BULLISH" | "BEARISH" | "";
  structure_level: number | null;
  displacement_atr_mult: number;
  confluence_breakdown: Record<string, number>;
  timeframe: string;
}

export interface SwingIdea {
  id: number;
  symbol: string;
  setup: string;
  entry_price: number;
  stop_loss: number;
  target_1: number | null;
  target_2: number | null;
  risk_reward: number;
  confidence_score: number;
  expected_holding_period: string;
  reasoning_summary: string;
  technical_signals: Record<string, string>;
  fundamental_signals: Record<string, string>;
  sentiment_signals: Record<string, string>;
  technical_factors: Record<string, unknown>;
  fundamental_factors: Record<string, unknown>;
  sentiment_factors: Record<string, unknown>;
  signal_first_detected_at: string | null;
  signals_updated_at: string | null;
  created_at: string;
  data_authenticity: string;
  status?: string;
  entry_type?: string;
  scan_cmp?: number | null;
  cmp_source?: string | null;
  cmp_age_sec?: number | null;
  entry_gap_pct?: number | null;
  action_tag?: string;
  smc_evidence?: SmcEvidence | null;
  sector?: string | null;
  target_source?: string | null;
  pe_ratio?: number | null;
  roe_pct?: number | null;
  roce_pct?: number | null;
  revenue_growth_pct?: number | null;
  debt_equity?: number | null;
  market_cap_cr?: number | null;
  promoter_pct?: number | null;
}

export interface LongTermIdea {
  id: number;
  symbol: string;
  setup: string;
  long_term_thesis: string;
  fair_value_estimate: number | null;
  entry_price: number;
  entry_zone: number[];
  stop_loss: number;
  long_term_target: number | null;
  risk_reward: number;
  risk_factors: string[];
  time_horizon: string;
  confidence_score: number;
  technical_signals: Record<string, string>;
  fundamental_signals: Record<string, string>;
  sentiment_signals: Record<string, string>;
  fundamental_factors: Record<string, unknown>;
  technical_factors: Record<string, unknown>;
  sentiment_factors: Record<string, unknown>;
  reasoning_summary: string;
  signal_first_detected_at: string | null;
  signals_updated_at: string | null;
  created_at: string;
  data_authenticity: string;
  status?: string;
  entry_type?: string;
  scan_cmp?: number | null;
  cmp_source?: string | null;
  cmp_age_sec?: number | null;
  entry_gap_pct?: number | null;
  action_tag?: string;
  smc_evidence?: SmcEvidence | null;
  sector?: string | null;
  target_source?: string | null;
  pe_ratio?: number | null;
  roe_pct?: number | null;
  roce_pct?: number | null;
  revenue_growth_pct?: number | null;
  debt_equity?: number | null;
  market_cap_cr?: number | null;
  promoter_pct?: number | null;
}

export interface StockSuggestion {
  symbol: string;
  name: string;
  exchange: string;
}

export interface StockAnalysisZone {
  type: string;
  bottom?: number;
  top?: number;
  level?: number;
}

export interface StockAnalysis {
  symbol: string;
  name: string;
  exchange: string;
  cmp: number | null;
  cmp_source?: string;
  cmp_age_sec?: number | null;
  entry_zone: number[] | null;
  stop_loss: number | null;
  target: number | null;
  risk_reward: number;
  confidence_score: number;
  setup_type: string;
  horizon: "SWING" | "LONGTERM" | string;
  recommendation: "Strong Buy" | "Watchlist" | "Avoid" | string;
  reason: string;
  criteria_not_met: string[];
  smc_zones: StockAnalysisZone[];
  fundamentals: {
    score?: number;
    pe_ratio?: number | null;
    roe_pct?: number | null;
    roce_pct?: number | null;
    revenue_growth_pct?: number | null;
    debt_equity?: number | null;
    market_cap_cr?: number | null;
    promoter_pct?: number | null;
    sector?: string | null;
    industry?: string | null;
    data_source?: string;
  };
  updated_at: string;
}

export interface RunningTradeMonitorItem {
  id: number;
  symbol: string;
  entry_price: number;
  current_price: number;
  cmp_source?: string | null;
  cmp_age_sec?: number | null;
  stop_loss: number;
  targets: number[];
  profit_loss: number;
  profit_loss_pct: number;
  drawdown: number;
  drawdown_pct: number;
  high_since_entry: number | null;
  low_since_entry: number | null;
  days_held: number;
  distance_to_target: number | null;
  distance_to_stop_loss: number | null;
  status: string;
  progress: number;
  progress_color: "red" | "yellow" | "green";
  created_at: string;
  updated_at: string;
}

export interface ResearchRunResponse {
  ok: boolean;
  scan: "swing" | "longterm";
  agent: string;
  status: string;
  /** Human-readable outcome (sync run or background accepted message) */
  summary?: string;
  message?: string;
  result: Record<string, unknown>;
}

// ── Watchlist OS (Phase 2) ─────────────────────────────────────────────────
export interface WatchlistProgressionStep {
  id?: string;
  label?: string;
  status?: "complete" | "pending" | "waiting";
}

/** Weighted pillar scores + lifecycle narrative from Watchlist OS intelligence engine */
export interface WatchlistIntelligence {
  pillar_scores?: Record<string, number>;
  readiness_weighted_pct?: number;
  readiness_delta_hint?: string | null;
  structure_quality?: string;
  momentum_quality?: string;
  setup_quality?: string;
  risk_grade?: string;
  setup_deteriorating?: boolean;
  stage_reason?: string;
  next_trigger?: string | null;
  invalidation_reason?: string | null;
  market_regime?: unknown;
}

/** Phase 5 / 5.5 decision intelligence — probabilistic, snapshot-derived */
export interface WatchlistDecisionIntel {
  setup_maturity_stage?: string;
  setup_maturity_score?: number;
  maturity_delta?: number;
  maturity_velocity?: number;
  lifecycle_probability_pct?: number;
  deterioration_probability_pct?: number;
  readiness_evolution?: {
    readiness_last_5m?: number;
    readiness_last_15m?: number;
    readiness_last_1h?: number;
    readiness_acceleration?: number;
    readiness_decay?: number;
    readiness_consistency?: number;
    evolution_regime?: string;
  };
  execution_quality?: { band?: string; score?: number };
  allocation?: { priority_score?: number; label?: string };
  deterioration?: {
    flags?: string[];
    false_breakout_probability_pct?: number;
    trap_probability_pct?: number;
    overall_risk_tone?: string;
    capital_protection_warning?: boolean;
  };
  deterioration_validation?: {
    validated_overall?: string;
    failed_continuation?: boolean;
    eject_from_portfolio?: boolean;
  };
  promotion?: Record<string, unknown>;
  signal_tier?: string;
  holding_period_hint?: string;
  confidence_calibration?: {
    inst_probability_pct?: number;
    high_probability_pct?: number;
    failure_probability_pct?: number;
    false_break_probability_pct?: number;
    calibration_samples_for_tier?: number;
    note?: string;
  };
  regimes?: { liquidity_regime?: string; volatility_regime?: string; macro_regime_hint?: string };
  execution_realism?: {
    distance_from_entry_pct?: number | null;
    rr_planned?: number | null;
    rr_effective_estimate?: number | null;
    rr_degradation?: number | null;
    slippage_risk_pct?: number;
    liquidity_risk_pct?: number;
    execution_delay_risk_pct?: number;
    volatility_expansion_risk_pct?: number;
    execution_posture?: string;
    trust_note?: string;
  };
  trade_levels?: {
    show_actionable_levels?: boolean;
    zone_reason?: string;
    monitoring_copy?: string | null;
  };
  user_action?: { primary_action?: string; rationale?: string };
  time_decay?: {
    stagnation_window_min?: number;
    readiness_stagnation?: boolean;
    watchlist_tenure_min?: number | null;
    setup_expired_hint?: boolean;
    momentum_aging_note?: string | null;
  };
  portfolio_competition_score?: number;
  confidence_language?: { lifecycle?: string; deterioration?: string; tiers?: string };
  note?: string;
}

/** Phase 6.5 — Personal desk dependency (backend retention_engine) */
export interface DeskOsPayload {
  evolving_rank?: number;
  urgency_state?: string;
  urgency_detail?: string;
  urgency_severity?: string;
  capital_priority_score?: number;
  deterioration_risk_trend?: string;
  confidence_evolution?: string;
  execution_timing_quality?: string;
  momentum_pillar?: number;
  nearest_actionable_trigger?: string;
  expected_opportunity_window?: string;
}

export interface WatchlistIntelItem {
  symbol: string;
  /** Redis LTP cache when available */
  quote_ltp?: number | null;
  horizon?: string | null;
  trend_state?: string;
  setup_status?: string;
  /** Clean monitor state for the redesigned watchlist UI: GOOD ENTRY | WATCH | AVOID | WEAK | BREAKOUT SOON | ACTIVE */
  monitor_state?: string;
  /** One-sentence professional summary — replaces the AI dump on the card */
  smart_sentence?: string;
  /** Institutional lifecycle label (DISCOVERY → ENTRY_READY → ACTIVE …) */
  lifecycle_stage?: string;
  current_stage?: string;
  /** True only when entry / SL / target may be shown as actionable */
  entry_ready?: boolean;
  intelligence?: WatchlistIntelligence;
  /** Raw progression steps readiness before pillar weighting */
  readiness_progression_pct?: number;
  /** Weighted readiness delta vs prior persisted snapshot (desk refresh). */
  readiness_delta_pct?: number | null;
  progression?: WatchlistProgressionStep[];
  readiness_pct?: number;
  conviction_pct?: number;
  ai_setup_score?: number;
  risk?: { rr?: number | null; volatility_hint?: string; notes?: string[] };
  recommendation?: {
    show_trade_levels?: boolean;
    entry_ready?: boolean;
    entry?: number | null;
    stop_loss?: number | null;
    target?: number | null;
    rationale?: string | null;
    monitoring_message?: string | null;
    nearest_trigger?: string | null;
    invalidation_reason?: string | null;
  };
  meta?: { has_research_row?: boolean; in_active_trade?: boolean; engine_tick_ts?: string | null };
  /** Row build identity for cross-tab / resync (Phase B). */
  sync_meta?: {
    row_as_of_ms?: number;
    global_state_version?: number;
    engine_snapshot_time?: string | null;
    ltp_source?: string;
  };
  decision?: WatchlistDecisionIntel;
  desk_os?: DeskOsPayload;
}

export interface WatchlistFeedEvent {
  ts?: string;
  symbol?: string;
  type?: string;
  headline?: string;
  setup_status?: string;
}

export interface DecisionPortfoliosPayload {
  intraday_momentum?: string[];
  mtf_swing?: string[];
  short_term_growth?: string[];
  long_term_compounders?: string[];
  slot_cap?: number;
  note?: string;
}

/** Phase 6 — Trader Command Center */
export interface PriorityFeedLine {
  headline: string;
  severity: string;
  symbol?: string | null;
  kind?: string;
}

export interface CommandCenterResponse {
  ok?: boolean;
  engine_version?: string;
  market_regime?: unknown;
  signals_today?: unknown;
  best_opportunities_now?: { symbol: string; note?: string; confidence_score?: number }[];
  biggest_improvements?: { symbol: string; readiness_delta_pct?: number; action?: string }[];
  biggest_deteriorations?: { symbol: string; deterioration_probability_pct?: number; validated_overall?: string; action?: string }[];
  active_high_conviction?: { symbol: string; confidence_score?: number; note?: string }[];
  portfolio_rotation?: { desks?: DecisionPortfoliosPayload; note?: string | null };
  alerts_requiring_attention?: { symbol: string; action: string; severity?: string }[];
  what_matters_now?: PriorityFeedLine[];
  watchlist_feed_preview?: WatchlistFeedEvent[];
  personal_desk_symbols?: number;
  premium?: { tier?: string; feed_limit?: number; full_watchlist_intel?: boolean; note?: string };
  trust_banner?: string;
  urgency_layer?: {
    rows?: { symbol?: string; label?: string; detail?: string; severity?: string }[];
    market_regime?: unknown;
    near_ready_count?: number;
    weakening_count?: number;
    trust_note?: string;
  };
  revisit_psychology?: {
    has_prior_visit?: boolean;
    lines?: string[];
    significant_changes?: number;
    last_visit_ts?: string | null;
    trust_note?: string;
  };
  session_intelligence_today?: {
    strongest_readiness_gains?: { symbol?: string; readiness_pct?: number; setup_status?: string }[];
    largest_deterioration_stress?: { symbol?: string; readiness_pct?: number; setup_status?: string }[];
    highest_momentum_names?: { symbol?: string; readiness_pct?: number; setup_status?: string }[];
    trust_note?: string;
  };
  _global_state_version?: number;
}

export interface DailyBriefSection {
  title: string;
  body: string;
}

export interface DailyBriefResponse {
  ok?: boolean;
  regime?: unknown;
  top_discovery_symbols?: string[];
  sections?: DailyBriefSection[];
  narrative_sections?: DailyBriefSection[];
  trust_note?: string;
  generated_at?: string;
}

export interface WatchlistOperatingResponse {
  ok?: boolean;
  engine_version?: string;
  items: WatchlistIntelItem[];
  feed: WatchlistFeedEvent[];
  retention?: {
    closest_to_trigger?: string[];
    strongest_scores?: string[];
    weakening?: string[];
    invalidated?: string[];
    best_rr?: string[];
    active?: string[];
    improving_readiness?: string[];
    momentum_expansion?: string[];
  };
  market_alignment?: Record<string, unknown>;
  counts?: { symbols?: number; with_research?: number };
  decision_portfolios?: DecisionPortfoliosPayload;
  decision_engine_version?: string;
  snapshot_stale?: boolean;
  snapshot_source?: string;
  hint?: string;
  _trust?: Record<string, unknown>;
  _global_state_version?: number;
  _snapshot_age_ms?: number | null;
}

// ── Portfolio types ────────────────────────────────────────────────────────
export interface PortfolioPosition {
  id: number;
  symbol: string;
  horizon: "SWING" | "LONGTERM";
  direction: "LONG" | "SHORT";
  entry_price: number;
  stop_loss: number;
  target_1: number | null;
  target_2: number | null;
  current_price: number | null;
  profit_loss: number;
  profit_loss_pct: number;
  drawdown: number;
  drawdown_pct: number;
  high_since_entry: number | null;
  low_since_entry: number | null;
  days_held: number;
  confidence_score: number;
  reasoning: string;
  status: "ACTIVE" | "TARGET_HIT" | "STOP_HIT" | "CLOSED" | "PARTIAL_EXIT";
  exit_price: number | null;
  exit_reason: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

export interface PortfolioJournalEntry {
  id: number;
  position_id: number;
  symbol: string;
  horizon: "SWING" | "LONGTERM";
  direction: string;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number | null;
  target_1: number | null;
  target_2: number | null;
  profit_loss: number;
  profit_loss_pct: number;
  days_held: number;
  exit_reason: string;
  created_at: string;
  closed_at: string;
}

export interface PortfolioJournalStats {
  total_trades: number;
  wins: number;
  losses: number;
  hit_rate_pct: number;
  avg_pnl_pct: number;
  total_pnl_pct: number;
  best_pnl_pct: number;
  worst_pnl_pct: number;
  avg_days_held: number;
}

export interface PortfolioBucketSummary {
  positions: PortfolioPosition[];
  count: number;
  max: number;
  journal_stats: PortfolioJournalStats;
}

export interface PortfolioSummary {
  swing: PortfolioBucketSummary;
  longterm: PortfolioBucketSummary;
  overall_stats: PortfolioJournalStats;
}

export interface ResearchCoverageRun {
  run_time: string | null;
  universe_requested: number;
  universe_scanned: number;
  quality_passed: number;
  ranked_candidates: number;
  selected_count: number;
  coverage_pct: number;
}

export interface ResearchCoverageResponse {
  target_universe: number;
  available_universe: number;
  returned_universe?: number;
  sources: Record<string, number>;
  cache_path?: string | null;
  cache_date?: string | null;
  source_errors?: Record<string, string> | null;
  latest: {
    SWING: ResearchCoverageRun | null;
    LONGTERM: ResearchCoverageRun | null;
  };
}

export interface ResearchValidationResponse {
  scan_id: string;
  horizon: "SWING" | "LONGTERM";
  coverage: LayerCoverageReport;
  funnel: LayerFunnelMetrics;
  logged_rows: number;
  items: Array<SwingIdea | LongTermIdea>;
  final_trades: Array<SwingIdea | LongTermIdea>;
  watchlist: Array<SwingIdea | LongTermIdea>;
  discovery: Array<SwingIdea | LongTermIdea>;
  fallback_items: Array<SwingIdea | LongTermIdea>;
  records_sample: Array<Record<string, unknown>>;
}

export interface ResearchDecisionCard {
  id?: number;
  symbol: string;
  setup?: string | null;
  section?: "final" | "watchlist" | "discovery" | string;
  entry_price?: number | null;
  stop_loss?: number | null;
  target_1?: number | null;
  target_2?: number | null;
  targets?: number[];
  risk_reward?: number | null;
  confidence_score: number;
  scan_cmp?: number | null;
  entry_type?: string | null;
  expected_holding_period?: string | null;
  layer1_pass?: boolean;
  layer2_pass?: boolean;
  layer3_pass?: boolean;
  final_selected?: boolean;
  near_setup?: boolean;
  rejection_reason?: string[];
  layer_details?: Record<string, unknown>;
  reasoning?: string;
  reasoning_summary?: string;
  technical_signals?: Record<string, string>;
  sector?: string | null;
  market_cap_cr?: number | null;
  action_tag?: string;
}

export interface ResearchDecisionFeedResponse {
  data_source: string;
  universe_size: number;
  scanned: number;
  returned: number;
  watchlist_returned: number;
  discovery_returned?: number;
  fallback_returned?: number;
  generated_at: string;
  scan_id: string;
  coverage: LayerCoverageReport;
  funnel: LayerFunnelMetrics;
  items: ResearchDecisionCard[];
  final_trades: ResearchDecisionCard[];
  watchlist: ResearchDecisionCard[];
  discovery: ResearchDecisionCard[];
  fallback_items?: ResearchDecisionCard[];
}

export interface LayerFunnelMetrics {
  total: number;
  layer1_pass: number;
  layer2_pass: number;
  layer3_pass: number;
  final_selected: number;
}

export interface LayerCoverageReport {
  total_universe?: number;
  available_universe?: number;
  scanned?: number;
  data_available?: number;
  missed?: number;
  coverage_percent?: number;
  missing_symbols?: string[];
  sources?: Record<string, number>;
}

export interface LayerReportRow {
  id: number;
  scan_id: string;
  horizon: "SWING" | "LONGTERM";
  symbol: string;
  date: string;
  cmp: number | null;
  entry: number | null;
  stop_loss: number | null;
  target: number | null;
  confidence: number;
  layer1_pass: number;
  layer2_pass: number;
  layer3_pass: number;
  final_selected: number;
  rejection_reason: string[];
  layer_details: Record<string, unknown>;
  coverage_report: LayerCoverageReport;
  created_at: string;
}

export interface LayerReportResponse {
  available: boolean;
  message?: string;
  scan_id?: string;
  horizon?: "SWING" | "LONGTERM";
  created_at?: string;
  funnel?: LayerFunnelMetrics;
  coverage?: LayerCoverageReport;
  rejection_counts?: Record<string, number>;
  sample?: LayerReportRow[];
}

// ── Research Performance & Journal interfaces ──────────────────────────────

export interface ResearchPickRow {
  symbol: string;
  entry_price: number;
  current_price: number | null;
  recommended_at: string;
  setup: string | null;
  confidence_score: number;
  profit_loss_pct: number;
  profit_loss: number;
  days_held: number;
  status: "RUNNING" | "TARGET_HIT" | "STOP_HIT" | "PENDING";
  high_since_entry: number | null;
  low_since_entry: number | null;
  updated_at: string | null;
}

export interface ResearchPerformanceSummary {
  total: number;
  active: number;
  target_hit: number;
  stop_hit: number;
  hit_rate_pct: number;
  avg_pnl_pct: number;
  best_pnl_pct: number;
  worst_pnl_pct: number;
  best_symbol: string | null;
  worst_symbol: string | null;
}

export interface ResearchPerformanceResponse {
  summary: ResearchPerformanceSummary;
  picks: ResearchPickRow[];
}

/** Phase 4C: stock_recommendations outcome rollup (drives "Research Hit Rate" card). */
export interface ResearchOutcomesSetupRow {
  setup: string;
  wins: number;
  losses: number;
  total: number;
  hit_rate_pct: number;
}

export interface ResearchOutcomes {
  horizon: "SWING" | "LONGTERM" | "ALL";
  window_days: number;
  total: number;
  active: number;
  target_hit: number;
  stop_hit: number;
  expired: number;
  resolved: number;
  hit_rate_pct: number;
  avg_pnl_r: number;
  profit_factor: number;
  by_setup: ResearchOutcomesSetupRow[];
}

export interface TrackRecordPick {
  id: number;
  symbol: string;
  agent_type: "SWING" | "LONGTERM";
  setup: string | null;
  status: string;
  entry_price: number;
  stop_loss: number | null;
  targets: number[];
  confidence_score: number;
  current_price: number | null;
  exit_price: number | null;
  exit_date: string | null;
  exit_reason: string | null;
  pnl_pct: number | null;
  days_held: number | null;
  high_since_entry: number | null;
  low_since_entry: number | null;
  created_at: string | null;
  signals_updated_at: string | null;
}

export interface TrackRecordSummary {
  total_picks: number;
  resolved: number;
  target_hit: number;
  stop_hit: number;
  hit_rate_pct: number;
  avg_pnl_pct: number;
  best_pnl_pct: number;
  worst_pnl_pct: number;
}

export interface TrackRecordResponse {
  picks: TrackRecordPick[];
  total: number;
  summary: TrackRecordSummary;
}

export interface ScanRunRow {
  run_time: string;
  horizon: "SWING" | "LONGTERM";
  universe_requested: number;
  universe_scanned: number;
  quality_passed: number;
  ranked_candidates: number;
  selected_count: number;
  notes: string | null;
}

export interface ResearchChartCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ResearchChartZone {
  top: number;
  bottom: number;
  zone_type: string;
  color: string;
  border_color: string;
  label: string;
}

export interface ResearchChartLevel {
  type: string;
  price: number;
  label: string;
  color: string;
  style: string;
  entry_type?: string;
}

export interface ResearchChartData {
  symbol: string;
  horizon: string;
  candles: ResearchChartCandle[];
  zones: ResearchChartZone[];
  levels: ResearchChartLevel[];
  setup: string;
  confidence: number;
  reasoning: string;
}

export interface ResearchAggregatePerformance {
  total_recommendations: number;
  active: number;
  target_hit: number;
  stop_hit: number;
  closed: number;
  resolved: number;
  hit_rate_pct: number;
  avg_closed_pnl_pct: number;
  avg_open_pnl_pct: number;
  total_pnl_pct: number;
  best_trade: { symbol: string; pnl_pct: number } | null;
  worst_trade: { symbol: string; pnl_pct: number } | null;
  avg_days_held: number;
  swing_scans: number;
  longterm_scans: number;
}

export interface ScanHistoryResponse {
  runs: ScanRunRow[];
  swing_count: number;
  longterm_count: number;
  total: number;
}

export interface ScanStatusResponse {
  in_flight: string[];
  horizons: Record<string, {
    status: string;
    started_at?: string;
    finished_at?: string;
    error?: string;
    summary?: string;
    agent?: string;
    trigger?: string;
  }>;
}

export interface PerformanceSnapshot {
  id: number;
  snapshot_date: string;
  horizon: "INTRADAY" | "SWING" | "LONGTERM" | "OVERALL";
  total_trades: number;
  win_count: number;
  loss_count: number;
  win_rate_pct: number;
  total_r: number;
  profit_factor: number;
  avg_pnl_pct: number;
  hit_rate_pct: number;
  best_symbol: string | null;
  worst_symbol: string | null;
  notes: string | null;
  created_at: string;
}

export interface JournalIdeaRow {
  id: number;
  symbol: string;
  setup: string | null;
  entry_price: number;
  stop_loss: number | null;
  targets: number[];
  confidence_score: number;
  expected_holding_period: string | null;
  reasoning_summary: string;
  recommended_at: string;
  current_price: number | null;
  profit_loss: number;
  profit_loss_pct: number;
  drawdown_pct: number;
  days_held: number;
  status: "RUNNING" | "TARGET_HIT" | "STOP_HIT" | "PENDING";
  high_since_entry: number | null;
  low_since_entry: number | null;
  updated_at: string | null;
}

export interface JournalIdeasPage {
  ideas: JournalIdeaRow[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  agent_type: string;
}

export interface JournalIdeasParams {
  symbol?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

// ── Market Intelligence Types ─────────────────────────────────────────────

export interface MIHoliday {
  date: string;
  name: string;
  country_code: string;
}

export interface MIFXSnapshot {
  usd_inr: number;
  usd_inr_prev: number | null;
  chg_pct: number;
  source: string;
  fetched_at: string;
}

export interface MIFREDMacro {
  fed_funds_rate: number | null;
  us_10y_yield: number | null;
  dxy_index: number | null;
  us_cpi_yoy: number | null;
  source: string;
  fetched_at: string;
}

export interface MIMFFlow {
  scheme_code: string;
  scheme_name: string;
  fund_house: string;
  nav: number;
  nav_date: string;
  nav_prev: number;
  chg_pct: number;
}

export interface MIMFFlowData {
  top_equity_funds: MIMFFlow[];
  fetched_at: string;
}

export interface MISnapshot {
  holidays: MIHoliday[];
  is_holiday_today: boolean;
  next_holiday: MIHoliday | null;
  fx: MIFXSnapshot | null;
  macro: MIFREDMacro | null;
  mf_flows: MIMFFlowData | null;
  fetched_at: string;
}

export interface MIHolidayResponse {
  holidays: MIHoliday[];
  is_holiday_today: boolean;
  next_holiday: MIHoliday | null;
  count: number;
}

// ── API Functions ─────────────────────────────────────────────────────────────

export const api = {
  // Live state
  snapshot:     () => get<EngineSnapshot>("/api/snapshot"),
  activeTrades: () => get<{ active_trades: ActiveTrade[] }>("/api/active-trades"),
  dailyPnl:     () => get<DailyPnL>("/api/daily-pnl"),
  zoneState:    () => get<{ zone_state: Record<string, ZoneEntry>; count: number }>("/api/zone-state"),
  engineStatus: () => get<{ engine_live: boolean; engine_mode: string; active_strategies: Record<string, boolean>; index_only: boolean; paper_mode: boolean }>("/api/engine-status"),

  // Analytics
  summary:     () => get<AnalyticsSummary>("/api/analytics/summary"),
  equityCurve: () => get<{ equity_curve: EquityPoint[] }>("/api/analytics/equity-curve"),
  bySetup:     () => get<{ setups: SetupStat[] }>("/api/analytics/by-setup"),
  rollingWR:   (w = 20) => get<{ window: number; data: RollingWRPoint[] }>(`/api/analytics/rolling-winrate?window=${w}`),
  calendar:    () => get<{ calendar: CalendarDay[] }>("/api/analytics/calendar-heatmap"),
  drawdown:    () => get<{ drawdown_events: DrawdownEvent[] }>("/api/analytics/drawdown-velocity"),
  timeOfDay:   () => get<{ hours: { hour: number; total: number; wins: number; win_rate: number; total_r: number }[] }>("/api/analytics/time-of-day"),
  syncStatus:  () => get<{ csv_exists: boolean; db_trade_count: number; last_sync: string | null }>("/api/analytics/sync-status"),
  forceSync:   () => post<{ status: string; rows_synced: number }>("/api/analytics/force-sync"),

  // Journal
  journal: (params: {
    symbol?: string; setup?: string; result?: string;
    direction?: string; date_from?: string; date_to?: string;
    limit?: number; offset?: number;
  }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && q.set(k, String(v)));
    return get<JournalPage>(`/api/journal?${q}`);
  },
  symbols:       () => get<{ symbols: string[] }>("/api/journal/symbols"),
  setups:        () => get<{ setups:  string[] }>("/api/journal/setups"),
  /** Telegram signal_log with optional filters; defaults to today if no dates passed (server local date). */
  signals: (params?: {
    date_from?: string;
    date_to?: string;
    symbol?: string;
    signal_kind?: string;
    limit?: number;
    offset?: number;
  }) => {
    const q = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return get<SignalLogPage>(`/api/journal/signals${qs ? `?${qs}` : ""}`);
  },
  /** Same data as a signals query for calendar today (backward compatible). */
  signalsToday:  () => get<{ signals: SignalLogEntry[]; count: number; total?: number; date: string; source: string }>("/api/journal/signals-today"),

  // ── Research Performance Analytics ────────────────────────────────────────
  swingPerformance: () => get<ResearchPerformanceResponse>("/api/analytics/research/swing-performance"),
  longtermPerformance: () => get<ResearchPerformanceResponse>("/api/analytics/research/longterm-performance"),
  scanHistory: (limit = 50) => get<ScanHistoryResponse>(`/api/analytics/research/scan-history?limit=${limit}`),
  performanceSnapshots: (horizon?: string, limit = 60) => {
    const q = new URLSearchParams();
    if (horizon) q.set("horizon", horizon);
    q.set("limit", String(limit));
    return get<{ snapshots: PerformanceSnapshot[] }>(`/api/analytics/performance-snapshots?${q}`);
  },

  // ── Journal: swing & long-term ideas ─────────────────────────────────────
  swingIdeas: (params?: JournalIdeasParams) => {
    const q = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => v !== undefined && q.set(k, String(v)));
    return get<JournalIdeasPage>(`/api/journal/swing-ideas?${q}`);
  },
  longtermIdeas: (params?: JournalIdeasParams) => {
    const q = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => v !== undefined && q.set(k, String(v)));
    return get<JournalIdeasPage>(`/api/journal/longterm-ideas?${q}`);
  },

  // AI Research Center
  /** Pass authToken when logged in so PREMIUM users get full lists (search/filter work on all ideas). */
  swingResearch: (limit = 12, authToken?: string | null) =>
    get<{ items: SwingIdea[]; count: number; gated?: boolean }>(`/api/research/swing?limit=${limit}`, authToken, 30_000),
  longtermResearch: (limit = 12, authToken?: string | null) =>
    get<{ items: LongTermIdea[]; count: number; last_scan_time?: string | null; slot_status?: { occupied: number; max: number; slots_full: boolean }; gated?: boolean }>(`/api/research/longterm?limit=${limit}`, authToken, 30_000),
  runningTradesResearch: (limit = 40) => get<{ items: RunningTradeMonitorItem[]; count: number }>(`/api/research/running-trades?limit=${limit}`),
  liveSignals: (limit = 40) => get<{ items: Array<{ signal_id?: string; symbol?: string; direction?: string; strategy_name?: string; entry?: number | null; stop_loss?: number | null; target1?: number | null; target2?: number | null; score?: number | null; confidence?: number | null; timestamp?: string; signal_kind?: string }>; count: number; source?: string }>(`/api/research/live-signals?limit=${limit}`),
  runningTradesHistory: (limit = 100) => get<{ items: RunningTradeMonitorItem[]; count: number }>(`/api/research/running-trades/history?limit=${limit}`),
  researchCoverage: (targetUniverse = 2200) => get<ResearchCoverageResponse>(`/api/research/coverage?target_universe=${targetUniverse}`),
  researchValidation: (horizon: "SWING" | "LONGTERM" = "SWING", topK = 10, targetUniverse = 2200) =>
    get<ResearchValidationResponse>(`/api/research/validation?horizon=${horizon}&top_k=${topK}&target_universe=${targetUniverse}`),
  researchDecisionFeed: (topK = 20, minTurnoverCr = 1) =>
    get<ResearchDecisionFeedResponse>(`/api/research/discovery?top_k=${topK}&min_turnover_cr=${minTurnoverCr}`),
  layerReport: (horizon: "SWING" | "LONGTERM" = "SWING", limit = 80) =>
    get<LayerReportResponse>(`/api/research/layer-report?horizon=${horizon}&limit=${limit}`),
  researchPerformance: () => get<ResearchAggregatePerformance>("/api/research/performance"),
  researchOutcomes: (horizon: "swing" | "longterm" | "all" = "swing", days = 30) =>
    get<ResearchOutcomes>(`/api/research/outcomes?horizon=${horizon}&days=${days}`),
  researchChartData: (symbol: string, horizon = "SWING") =>
    get<ResearchChartData>(`/api/research/chart-data/${encodeURIComponent(symbol)}?horizon=${horizon}`),
  stockSuggestions: (q: string, limit = 10) =>
    get<{ items: StockSuggestion[] }>(`/api/search-stock/suggestions?q=${encodeURIComponent(q)}&limit=${limit}`),
  searchStock: (symbol: string) =>
    get<StockAnalysis>(`/api/search-stock?symbol=${encodeURIComponent(symbol)}`),
  runSwingScan: () => post<ResearchRunResponse>("/api/research/run/swing"),
  runLongtermScan: () => post<ResearchRunResponse>("/api/research/run/longterm"),
  trackRecord: (horizon: "swing" | "longterm" | "all" = "all", limit = 100) =>
    get<TrackRecordResponse>(`/api/research/track-record?horizon=${horizon}&limit=${limit}`),
  submitResearchEmailLead: (email: string) => post<{ ok: boolean }>("/api/research/lead", { email }),
  scanStatus: () => get<ScanStatusResponse>("/api/research/scan-status"),
  trackerRefresh: () => post<{ ok: boolean; seeded: number; updated: number }>("/api/research/tracker/refresh"),

  // ── Portfolio (persistent positions) ──────────────────────────────────────
  portfolioSummary: () => get<PortfolioSummary>("/api/portfolio/summary"),
  portfolioSwing: (limit = 10) => get<{ items: PortfolioPosition[]; count: number; max: number; horizon: string }>(`/api/portfolio/swing?limit=${limit}`),
  portfolioLongterm: (limit = 10) => get<{ items: PortfolioPosition[]; count: number; max: number; horizon: string }>(`/api/portfolio/longterm?limit=${limit}`),
  portfolioCounts: () => get<{ swing: number; swing_max: number; longterm: number; longterm_max: number }>("/api/portfolio/counts"),
  portfolioJournal: (horizon?: string, limit = 50) => {
    const q = new URLSearchParams();
    if (horizon) q.set("horizon", horizon);
    q.set("limit", String(limit));
    return get<{ items: PortfolioJournalEntry[]; count: number }>(`/api/portfolio/journal/all?${q}`);
  },
  portfolioJournalStats: (horizon?: string) => {
    const q = horizon ? `?horizon=${horizon}` : "";
    return get<PortfolioJournalStats>(`/api/portfolio/journal/stats${q}`);
  },
  portfolioAutoPromote: () => post<{ ok: boolean; promoted: { swing: number; longterm: number } }>("/api/portfolio/auto-promote"),
  portfolioSeed: () => post<{ ok: boolean; seeded: number }>("/api/portfolio/seed"),
  portfolioRefreshPrices: () => post<{ ok: boolean; updated: number }>("/api/portfolio/refresh-prices"),
  portfolioClosePosition: (positionId: number, exitPrice: number, exitReason = "MANUAL") =>
    post<{ ok: boolean; symbol: string; pnl_pct: number }>(`/api/portfolio/${positionId}/close`, { exit_price: exitPrice, exit_reason: exitReason }),

  // ── Watchlist ──────────────────────────────────────────────────────────────
  getWatchlist: (token: string) =>
    fetch(`${BASE}/api/watchlist`, { cache: "no-store", headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.ok ? r.json() as Promise<{ items: { symbol: string; added_at: string }[] }> : Promise.reject()),
  addToWatchlist: (token: string, symbol: string) =>
    fetch(`${BASE}/api/watchlist`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    }).then((r) => r.json()),
  removeFromWatchlist: (token: string, symbol: string) =>
    fetch(`${BASE}/api/watchlist/${encodeURIComponent(symbol)}`, {
      method: "DELETE", headers: { Authorization: `Bearer ${token}` },
    }).then((r) => r.json()),

  /** Take Entry — open an active position from the watchlist. */
  takeEntry: (
    token: string,
    payload: {
      symbol: string;
      entry_price: number;
      stop_loss?: number | null;
      target_1?: number | null;
      target_2?: number | null;
      holding_period?: string;
      notes?: string;
    },
  ) =>
    fetch(`${BASE}/api/watchlist/positions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => r.json()),

  listPositions: (token: string, status: "ACTIVE" | "CLOSED" | "ALL" = "ACTIVE") =>
    fetch(`${BASE}/api/watchlist/positions?status=${status}`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
    }).then((r) => r.json() as Promise<{
      items: Array<{
        id: number;
        symbol: string;
        entry_price: number;
        stop_loss: number | null;
        target_1: number | null;
        target_2: number | null;
        holding_period: string | null;
        taken_at: string;
        status: string;
        live_status: string | null;
        cmp: number | null;
        cmp_source: string | null;
        pnl_pct: number | null;
        pnl_r: number | null;
        holding_days: number | null;
        exit_price: number | null;
        exit_reason: string | null;
        exited_at: string | null;
        notes: string | null;
      }>;
      count: number;
      status_filter: string;
    }>),

  closePosition: (token: string, positionId: number, body?: { exit_price?: number; exit_reason?: string }) =>
    fetch(`${BASE}/api/watchlist/positions/${positionId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then((r) => r.json()),

  /** Phase 2 — AI watchlist operating system (enriched items + feed + retention). */
  getWatchlistOperating: (token: string) =>
    get<WatchlistOperatingResponse>("/api/watchlist/operating", token),
  getWatchlistFeed: (token: string, limit = 40) =>
    get<{ ok: boolean; items: WatchlistFeedEvent[] }>(
      `/api/watchlist/feed?limit=${encodeURIComponent(String(limit))}`,
      token
    ),

  /** Phase 6 — Command Center + retention hooks */
  commandCenter: (token?: string | null) =>
    get<CommandCenterResponse>("/api/command-center", token ?? undefined),
  dailyBrief: (token?: string | null) =>
    get<DailyBriefResponse>("/api/market/daily-brief", token ?? undefined),
  visitMark: (token: string) =>
    fetch(`${BASE}/api/user/visit-mark`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }).then(async (r) => {
      if (!r.ok) throw new Error(`visit-mark ${r.status}`);
      return r.json() as Promise<{ ok: boolean }>;
    }),
  revisitSummary: (token: string) =>
    get<{
      ok: boolean;
      lines?: string[];
      has_prior_visit?: boolean;
      significant_changes?: number;
      last_visit_ts?: string | null;
      trust_note?: string;
    }>("/api/user/revisit-summary", token),
  evolutionTimeline: (token: string, symbol: string, limit = 32) =>
    get<{
      ok: boolean;
      symbol?: string;
      items: Array<{ ts?: string; headline?: string; kind?: string }>;
      cap?: number;
      premium?: boolean;
    }>(
      `/api/user/evolution/${encodeURIComponent(symbol)}?limit=${encodeURIComponent(String(limit))}`,
      token
    ),
  sessionPulse: (token: string) =>
    fetch(`${BASE}/api/user/session-pulse`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }).then(async (r) => {
      if (!r.ok) throw new Error(`session-pulse ${r.status}`);
      return r.json() as Promise<{ ok: boolean }>;
    }),
  intelEvent: (token: string, body: { event: string; symbol?: string }) =>
    fetch(`${BASE}/api/user/intel/event`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    }).then(async (r) => {
      if (!r.ok) throw new Error(`intel-event ${r.status}`);
      return r.json() as Promise<{ ok: boolean; stored?: boolean }>;
    }),

  // ── Market Intelligence ───────────────────────────────────────────────────
  marketIntelSnapshot: () => get<MISnapshot>("/api/market-intelligence/snapshot"),
  marketIntelHolidays: (year?: number) => {
    const q = year ? `?year=${year}` : "";
    return get<MIHolidayResponse>(`/api/market-intelligence/holidays${q}`);
  },
  marketIntelMacro: () => get<MIFREDMacro>("/api/market-intelligence/macro"),
  marketIntelFX: () => get<MIFXSnapshot>("/api/market-intelligence/fx"),
  marketIntelMFFlows: () => get<MIMFFlowData>("/api/market-intelligence/mf-flows"),
};
