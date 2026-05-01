"use client";

/**
 * Phase 3 — useTerminalSummary
 *
 * Lightweight poller for /api/summary, /api/preferences, /api/performance.
 * Refreshes summary every 45s. Preferences + performance are loaded once on
 * mount and on demand via the returned `refresh*` callbacks.
 */

import { useCallback, useEffect, useState } from "react";
import { getBackendBase } from "@/lib/api";

export interface AISummaryPayload {
  market_bias: string;
  headline: string;
  best_opportunity: SummaryCard | null;
  top_trades: SummaryCard[];
  totals: {
    count: number;
    long: number;
    short: number;
    avg_quality: number;
    avg_probability: number;
  };
}

export interface SummaryCard {
  symbol: string;
  direction: "LONG" | "SHORT";
  setup: string;
  confidence: string;
  probability: number;
  quality_score: number;
  rr: number | null;
  risk_level: string;
  expected_outcome: string;
  expected_move_time: string;
  action?: string;
  conviction?: string;
  narrative?: string;
}

export interface UserPreferences {
  risk_preference: "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE";
  capital: number;
  risk_per_trade_pct: number;
  min_rr: number;
  min_probability: number;
  preferred_setups: string[];
  setups_strict: boolean;
  direction: "LONG" | "SHORT" | "BOTH";
  alerts: {
    approaching: boolean;
    triggered: boolean;
    target_hit: boolean;
    stop_hit: boolean;
    telegram: boolean;
  };
}

export interface PerformanceStats {
  total_trades: number;
  open_trades: number;
  win_rate: number;
  avg_rr: number;
  total_pnl: number;
  wins?: number;
  losses?: number;
  best_trade?: { symbol: string; pnl: number; rr: number | null } | null;
  worst_trade?: { symbol: string; pnl: number; rr: number | null } | null;
  by_setup?: Array<{ setup: string; count: number; wins: number; pnl: number; win_rate: number; avg_rr: number }>;
  best_setup?: string | null;
  worst_setup?: string | null;
}

export interface DailyPnL {
  date: string;
  realized_r: number;
  total_pnl: number;
  wins: number;
  losses: number;
  total: number;
  win_rate: number;
  streak: number;
  trades: Array<{ symbol: string; direction: string; pnl: number; rr?: number | null; setup?: string }>;
}

const SUMMARY_REFRESH_MS = 45_000;
const PNL_REFRESH_MS = 60_000;

export function useTerminalSummary() {
  const base = getBackendBase();
  const [summary, setSummary] = useState<AISummaryPayload | null>(null);
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [perf, setPerf] = useState<PerformanceStats | null>(null);
  const [dailyPnl, setDailyPnl] = useState<DailyPnL | null>(null);

  const fetchSummary = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/summary`, { cache: "no-store" });
      if (res.ok) setSummary(await res.json());
    } catch {
      /* ignore */
    }
  }, [base]);

  const fetchPrefs = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/preferences`, { cache: "no-store" });
      if (res.ok) setPrefs(await res.json());
    } catch {
      /* ignore */
    }
  }, [base]);

  const savePrefs = useCallback(
    async (next: Partial<UserPreferences>) => {
      try {
        const res = await fetch(`${base}/api/preferences`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...(prefs ?? {}), ...next }),
        });
        if (res.ok) setPrefs(await res.json());
      } catch {
        /* ignore */
      }
    },
    [base, prefs],
  );

  const fetchPerf = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/performance`, { cache: "no-store" });
      if (res.ok) setPerf(await res.json());
    } catch {
      /* ignore */
    }
  }, [base]);

  const fetchDailyPnl = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/pnl/daily`, { cache: "no-store" });
      if (res.ok) setDailyPnl(await res.json());
    } catch {
      /* ignore */
    }
  }, [base]);

  const markTaken = useCallback(
    async (symbol: string, opts?: { qty?: number; notes?: string; confidence?: number }) => {
      try {
        const res = await fetch(`${base}/api/trades/${encodeURIComponent(symbol)}/taken`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(opts ?? {}),
        });
        if (res.ok) {
          // Refresh perf + pnl after marking
          fetchPerf();
          fetchDailyPnl();
          return (await res.json()) as { ok: boolean; duplicate: boolean; entry: Record<string, unknown> };
        }
        return null;
      } catch {
        return null;
      }
    },
    [base, fetchPerf, fetchDailyPnl],
  );

  useEffect(() => {
    fetchSummary();
    fetchPrefs();
    fetchPerf();
    fetchDailyPnl();
    const id1 = setInterval(fetchSummary, SUMMARY_REFRESH_MS);
    const id2 = setInterval(fetchDailyPnl, PNL_REFRESH_MS);
    return () => {
      clearInterval(id1);
      clearInterval(id2);
    };
  }, [fetchSummary, fetchPrefs, fetchPerf, fetchDailyPnl]);

  return { summary, prefs, perf, dailyPnl, refreshSummary: fetchSummary, refreshPerf: fetchPerf, savePrefs, markTaken };
}
