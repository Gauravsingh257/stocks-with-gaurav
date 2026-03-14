import { BarChart2, Sunrise, ListChecks, ShieldAlert, Bot } from "lucide-react";
import type { ReactNode } from "react";

// ── Types ────────────────────────────────────────────────────────────────────
export interface AgentStatus {
  name: string;
  description: string;
  schedule: string;
  priority: string;
  last_run?: { run_time: string; status: string; summary: string } | null;
  next_run?: string | null;
  error?: string;
}

export interface ActionItem {
  id: number;
  agent_name: string;
  action_type: string;
  payload?: Record<string, unknown>;
  status: string;
  requires_approval: boolean;
  created_at: string;
}

export interface LogItem {
  id: number;
  agent_name: string;
  run_time: string;
  status: string;
  summary: string;
  findings?: unknown[];
  actions?: unknown[];
  metrics?: Record<string, unknown>;
  findings_json?: unknown[];
  actions_json?: unknown[];
  metrics_json?: Record<string, unknown>;
}

export interface HealthData {
  backend_version: string;
  engine_version: string;
  agent_version: string;
  engine_live: boolean;
  engine_mode: string;
  db_connected: boolean;
  db_trade_rows: number;
  ws_clients: number;
  scheduler_running: boolean;
  last_snapshot: string | null;
  uptime_human: string;
}

export interface TacticalPlan {
  date: string;
  generated_at?: string;
  mode: string;
  mode_description: string;
  risk_multiplier: number;
  max_daily_risk: number;
  score_threshold: number;
  stop_after_losses: number;
  focus_setups: string[];
  disable_setups: string[];
  market_condition: string;
  market_regime: string;
  confidence: number;
  wr_state: string;
  dd_state: string;
  cl_state: string;
  inputs?: Record<string, unknown>;
}

export interface Toast {
  id: number;
  type: "success" | "error" | "warning" | "info";
  title: string;
  body?: string;
}

export interface AgentMeta {
  icon: ReactNode;
  color: string;
  glow: string;
}

// ── Constants ────────────────────────────────────────────────────────────────
export const AGENT_META: Record<string, AgentMeta> = {
  PostMarketAnalyst: { icon: <BarChart2 size={16} />,  color: "#00d4ff", glow: "rgba(0,212,255,0.2)" },
  PreMarketBriefing: { icon: <Sunrise size={16} />,    color: "#00ff88", glow: "rgba(0,255,136,0.2)" },
  TradeManager:      { icon: <ListChecks size={16} />, color: "#ffd700", glow: "rgba(255,215,0,0.2)" },
  RiskSentinel:      { icon: <ShieldAlert size={16} />, color: "#ff4d6d", glow: "rgba(255,77,109,0.2)" },
};

export const DEFAULT_META: AgentMeta = { icon: <Bot size={16} />, color: "var(--accent)", glow: "transparent" };

// ── Helpers ──────────────────────────────────────────────────────────────────
export function fmtTime(iso?: string | null) {
  if (!iso) return "\u2014";
  try { return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}

export function fmtDateTime(iso?: string | null) {
  if (!iso) return "\u2014";
  try { return new Date(iso).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" }); }
  catch { return iso; }
}

export function statusColor(s: string) {
  if (s === "OK")      return "#00ff88";
  if (s === "WARNING") return "#ffd700";
  if (s === "ERROR")   return "#ff4d6d";
  if (s === "RUNNING") return "#00d4ff";
  return "var(--text-secondary)";
}

export function statusDot(s?: string | null, running?: boolean) {
  if (running) return { color: "#00d4ff", label: "RUNNING", pulse: true };
  if (!s)      return { color: "#666",    label: "IDLE",    pulse: false };
  if (s === "OK")      return { color: "#00ff88", label: "OK",      pulse: false };
  if (s === "WARNING") return { color: "#ffd700", label: "WARNING", pulse: true };
  if (s === "ERROR")   return { color: "#ff4d6d", label: "ERROR",   pulse: true };
  return { color: "#666", label: s, pulse: false };
}

export const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";
