/**
 * Unified market LTP overlay (indices + equities) for TopBar / watchlist / strips.
 * Survives reconnect; stream ordering is enforced in useWebSocket before merging here.
 */

import { useEffect, useMemo, useState } from "react";

let marketLtpOverlay: Record<string, number> = {};
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* ignore */
    }
  });
}

/** Merge WS LTP patch (any symbol or index label) into the shared overlay. */
export function mergeMarketLtpOverlay(patch: Record<string, number> | undefined): void {
  if (!patch || !Object.keys(patch).length) return;
  marketLtpOverlay = { ...marketLtpOverlay, ...patch };
  emit();
}

/** @deprecated use mergeMarketLtpOverlay */
export const mergeIndexLtpOverlay = mergeMarketLtpOverlay;

/**
 * Authoritative bundle from snapshot / resync: rebuild overlay from index + equity baselines.
 */
export function replaceMarketLtpFromSnapshot(
  indexPatch?: Record<string, number> | null,
  equityPatch?: Record<string, number> | null
): void {
  const next = { ...(indexPatch || {}), ...(equityPatch || {}) };
  if (!Object.keys(next).length) return;
  marketLtpOverlay = { ...next };
  emit();
}

/** @deprecated use replaceMarketLtpFromSnapshot(index, undefined) */
export function replaceIndexLtpFromSnapshot(patch: Record<string, number> | undefined): void {
  replaceMarketLtpFromSnapshot(patch ?? null, null);
}

export function getMergedMarketLtp(
  indexBaseline?: Record<string, number> | null,
  equityBaseline?: Record<string, number> | null
): Record<string, number> {
  return {
    ...(indexBaseline || {}),
    ...(equityBaseline || {}),
    ...marketLtpOverlay,
  };
}

/** Index-first helper; overlay may still contain equity keys from unified WS LTP. */
export function getMergedIndexLtp(restBaseline?: Record<string, number> | null): Record<string, number> {
  return getMergedMarketLtp(restBaseline, null);
}

export function useMergedMarketLtp(
  indexBaseline?: Record<string, number> | null,
  equityBaseline?: Record<string, number> | null
): Record<string, number> {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const u = () => setTick((x) => x + 1);
    listeners.add(u);
    return () => {
      listeners.delete(u);
    };
  }, []);
  return useMemo(() => getMergedMarketLtp(indexBaseline, equityBaseline), [indexBaseline, equityBaseline, tick]);
}

export function useMergedIndexLtp(restBaseline?: Record<string, number> | null): Record<string, number> {
  return useMergedMarketLtp(restBaseline, null);
}
