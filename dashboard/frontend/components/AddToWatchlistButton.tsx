"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { BookmarkPlus, Check } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";

/** Cloud API only — adds symbol to user_watchlist via Railway backend. */
export default function AddToWatchlistButton({
  symbol,
  compact = false,
  onAdded,
}: {
  symbol: string;
  compact?: boolean;
  /** Called after successful POST (e.g. router.refresh for /watchlist). */
  onAdded?: () => void;
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
    try {
      await api.addToWatchlist(token, clean);
      setDone(true);
      onAdded?.();
    } catch {
      setErr("Could not save");
    } finally {
      setBusy(false);
    }
  }, [token, clean]);

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
