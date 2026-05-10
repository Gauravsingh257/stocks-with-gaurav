/**
 * Single shared LTP/index overlay for TopBar / watchlist / strips — ordered WS updates win.
 * Survives reconnect (no blanking); sequence checks happen in useWebSocket before merging here.
 */

import { useEffect, useMemo, useState } from "react";

let indexLtpOverlay: Record<string, number> = {};
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

export function mergeIndexLtpOverlay(patch: Record<string, number> | undefined): void {
  if (!patch || !Object.keys(patch).length) return;
  indexLtpOverlay = { ...indexLtpOverlay, ...patch };
  emit();
}

/** Full snapshot replaces overlay keys present on engine tick (authoritative bundle). */
export function replaceIndexLtpFromSnapshot(patch: Record<string, number> | undefined): void {
  if (!patch || !Object.keys(patch).length) return;
  indexLtpOverlay = { ...patch };
  emit();
}

export function getMergedIndexLtp(restBaseline?: Record<string, number> | null): Record<string, number> {
  return { ...(restBaseline || {}), ...indexLtpOverlay };
}

export function useMergedIndexLtp(restBaseline?: Record<string, number> | null): Record<string, number> {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const u = () => setTick((x) => x + 1);
    listeners.add(u);
    return () => {
      listeners.delete(u);
    };
  }, []);
  return useMemo(() => getMergedIndexLtp(restBaseline), [restBaseline, tick]);
}
