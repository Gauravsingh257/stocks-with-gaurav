"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { BookmarkPlus, Check } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";

/**
 * Adds a symbol to the watchlist.
 *  - With a `setup` (entry/SL/targets from a research card) → creates a full
 *    watchlist_positions row via /api/watchlist/monitor (source=RESEARCH_AUTO)
 *    so it appears as an active WatchlistMonitor card with entry zone + actions.
 *  - Without a setup (plain symbol) → legacy /api/watchlist symbol add.
 */
export interface WatchlistSetup {
  entry_price?: number | null;
  stop_loss?: number | null;
  target_1?: number | null;
  target_2?: number | null;
  pattern?: string | null;
}

// Entry zone derived from a research card's single entry price (±band).
const ENTRY_BAND = 0.01; // ±1%

export default function AddToWatchlistButton({
  symbol,
  compact = false,
  onAdded,
  setup,
}: {
  symbol: string;
  compact?: boolean;
  /** Called after successful POST (e.g. router.refresh for /watchlist). */
  onAdded?: () => void;
  /** When provided, creates a full monitor entry (entry zone + SL + targets). */
  setup?: WatchlistSetup;
}) {
  const { user, token } = useAuth();
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const clean = symbol.replace(/^NSE:/i, "").trim().toUpperCase();

  const onAdd = useCallback(async () => {
    if (!token || !clean) return;
    setBusy(true);
    setErr(null);
    setDone(true);
    try {
      const entry = setup?.entry_price;
      const sl = setup?.stop_loss;
      if (entry && sl) {
        // Full research setup → active monitor entry with levels copied.
        await api.watchlistMonitorAdd(token, {
          symbol: clean,
          entry_low: Number((entry * (1 - ENTRY_BAND)).toFixed(2)),
          entry_high: Number((entry * (1 + ENTRY_BAND)).toFixed(2)),
          stop_loss: Number(sl),
          target_1: setup?.target_1 ?? null,
          target_2: setup?.target_2 ?? null,
          pattern: setup?.pattern ?? null,
          source: "RESEARCH_AUTO",
        });
      } else {
        await api.addToWatchlist(token, clean);
      }
      onAdded?.();
    } catch {
      setDone(false);
      setErr("Could not save");
    } finally {
      setBusy(false);
    }
  }, [token, clean, onAdded, setup]);

  if (!user || !token) {
    return (
      <Link
        href="/login"
        className="btn-accent"
        style={{ textDecoration: "none", fontSize: compact ? "0.68rem" : "0.74rem", padding: compact ? "5px 10px" : "7px 12px", fontWeight: 800 }}
      >
        Sign in to save
      </Link>
    );
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        onClick={onAdd}
        disabled={busy || done}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: compact ? 4 : 6,
          padding: compact ? "5px 10px" : "7px 12px",
          borderRadius: 8,
          border: done ? "1px solid rgba(0,224,150,0.35)" : "1px solid rgba(245,158,11,0.35)",
          background: done ? "rgba(0,224,150,0.1)" : "rgba(245,158,11,0.08)",
          color: done ? "var(--success)" : "var(--warning)",
          fontSize: compact ? "0.68rem" : "0.74rem",
          fontWeight: 800,
          cursor: busy ? "wait" : done ? "default" : "pointer",
          opacity: busy ? 0.7 : 1,
        }}
      >
        {done ? <Check size={14} /> : <BookmarkPlus size={14} />}
        {done ? "In watchlist" : busy ? "Saving…" : "Add to watchlist"}
      </button>
      {err && <span style={{ fontSize: "0.62rem", color: "var(--danger)" }}>{err}</span>}
    </span>
  );
}
