/**
 * Unified realtime envelope types (Railway WS + REST snapshots).
 */

import type { EngineSnapshot } from "../api";

export interface SnapshotEnvelopeFields {
  _global_state_version?: number;
  _snapshot_ts?: string;
  _snapshot_age_ms?: number | null;
  _snapshot_origin?: string;
}

export interface WsEnvelope {
  type: string;
  ws_event_kind?: string;
  event_id?: string;
  event_ts?: number;
  global_state_version?: number;
  snapshot_version?: number;
  entity_version?: number;
  data?: unknown;
  /** Thin watchlist hint */
  user_id?: number;
  kind?: string;
}

export type StateEngineMetrics = {
  lastGlobalVersion: number;
  rejectedStale: number;
  forcedResyncs: number;
  lastReconcileAt: number;
  queueDepth: number;
};
