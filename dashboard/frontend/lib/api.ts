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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST" });
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body?.detail ? `: ${body.detail}` : "";
    } catch {
      // ignore parse error
    }
    throw new Error(`API ${path} → ${res.status}${detail}`);
  }
  return res.json() as Promise<T>;
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
  snapshot_time:       string;
  setup_d_state?:      Record<string, SetupDEntry>;
  adaptive_intel?:     AdaptiveIntel;
  /** Index LTP from cache (NIFTY 50, NIFTY BANK) for command bar / sparklines */
  index_ltp?:          Record<string, number>;
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
  created_at: string;
}

export interface LongTermIdea {
  id: number;
  symbol: string;
  long_term_thesis: string;
  fair_value_estimate: number | null;
  entry_zone: number[];
  long_term_target: number | null;
  risk_factors: string[];
  time_horizon: string;
  confidence_score: number;
  technical_signals: Record<string, string>;
  fundamental_signals: Record<string, string>;
  sentiment_signals: Record<string, string>;
  fundamental_factors: Record<string, unknown>;
  technical_factors: Record<string, unknown>;
  sentiment_factors: Record<string, unknown>;
  created_at: string;
}

export interface RunningTradeMonitorItem {
  id: number;
  symbol: string;
  entry_price: number;
  current_price: number;
  stop_loss: number;
  targets: number[];
  profit_loss: number;
  drawdown: number;
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
  summary: string;
  result: Record<string, unknown>;
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
  sources: Record<string, number>;
  latest: {
    SWING: ResearchCoverageRun | null;
    LONGTERM: ResearchCoverageRun | null;
  };
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

  // AI Research Center
  swingResearch: (limit = 12) => get<{ items: SwingIdea[]; count: number }>(`/api/research/swing?limit=${limit}`),
  longtermResearch: (limit = 12) => get<{ items: LongTermIdea[]; count: number }>(`/api/research/longterm?limit=${limit}`),
  runningTradesResearch: (limit = 40) => get<{ items: RunningTradeMonitorItem[]; count: number }>(`/api/research/running-trades?limit=${limit}`),
  researchCoverage: (targetUniverse = 1800) => get<ResearchCoverageResponse>(`/api/research/coverage?target_universe=${targetUniverse}`),
  runSwingScan: () => post<ResearchRunResponse>("/api/research/run/swing"),
  runLongtermScan: () => post<ResearchRunResponse>("/api/research/run/longterm"),
};
