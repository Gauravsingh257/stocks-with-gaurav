"use client";
import { useEffect, useState } from "react";

export interface HealthData {
  db_connected?: boolean;
  ws_clients?: number;
  engine_live?: boolean;
  engine_status?: string;
  backend_version?: string;
  engine_version?: string;
  uptime_human?: string;
  kite_connected?: boolean;
  token_present?: boolean;
  token_expires_in_hours?: number | null;
}

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

let _cached: HealthData | null = null;
let _listeners = new Set<(h: HealthData | null) => void>();
let _timer: ReturnType<typeof setInterval> | null = null;

function fetchHealth() {
  if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
  fetch(`${BASE}/api/system/health`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d) {
        _cached = d;
        _listeners.forEach((cb) => cb(d));
      }
    })
    .catch(() => {});
}

function startPolling() {
  if (_timer) return;
  fetchHealth();
  _timer = setInterval(fetchHealth, 30_000);
}

function stopPolling() {
  if (_timer) {
    clearInterval(_timer);
    _timer = null;
  }
}

export function useHealth(): HealthData | null {
  const [health, setHealth] = useState<HealthData | null>(_cached);

  useEffect(() => {
    _listeners.add(setHealth);
    if (_listeners.size === 1) startPolling();
    return () => {
      _listeners.delete(setHealth);
      if (_listeners.size === 0) stopPolling();
    };
  }, []);

  return health;
}
