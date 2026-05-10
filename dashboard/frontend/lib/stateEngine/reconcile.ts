/**
 * Snapshot / delta reconciliation — never blindly apply stale or regressive versions.
 */

import type { EngineSnapshot } from "../api";
import type { WsEnvelope } from "./types";

const STALE_AGE_MS = 180_000;

export function mergeSnapshot(_prev: EngineSnapshot | null, incoming: EngineSnapshot): EngineSnapshot {
  return incoming;
}

export function mergeDigestPatch(
  prev: EngineSnapshot | null,
  patch: Record<string, unknown>
): EngineSnapshot | null {
  if (!prev) {
    return { ...(patch as unknown as EngineSnapshot) };
  }
  const { digest: _d, active_symbols_sample: _s, _estimate_full_bytes: _e, ...rest } = patch;
  return { ...prev, ...(rest as Partial<EngineSnapshot>) };
}

export function rejectStaleUpdate(
  lastVersion: number,
  incomingVersion: number | undefined
): boolean {
  if (incomingVersion === undefined || incomingVersion === null) return false;
  return incomingVersion < lastVersion;
}

export function rejectStaleSnapshotAge(ageMs: number | null | undefined): boolean {
  if (ageMs === undefined || ageMs === null) return false;
  return ageMs > STALE_AGE_MS;
}

export function extractEnvelopeVersion(msg: WsEnvelope): number | undefined {
  if (typeof msg.global_state_version === "number") return msg.global_state_version;
  if (typeof msg.snapshot_version === "number") return msg.snapshot_version;
  return undefined;
}

/**
 * Strict transport ordering: reject duplicate / replayed frames (older sequence).
 * On WebSocket reconnect, caller resets lastSeq to 0 so new server epochs apply.
 */
export function rejectOutOfOrderStream(
  lastSeq: number,
  incomingSeq: number | undefined
): boolean {
  if (incomingSeq === undefined || incomingSeq === null) return false;
  return incomingSeq < lastSeq;
}

export function nextStreamCursor(
  lastSeq: number,
  incomingSeq: number | undefined
): number {
  if (incomingSeq === undefined || incomingSeq === null) return lastSeq;
  return Math.max(lastSeq, incomingSeq);
}

export function extractDataSnapshot(data: unknown): EngineSnapshot | null {
  if (data && typeof data === "object") return data as EngineSnapshot;
  return null;
}
