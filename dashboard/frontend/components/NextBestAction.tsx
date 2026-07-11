"use client";

/**
 * NextBestAction — the "no dead ends" card. Drops onto Research / Watchlist /
 * Terminal (and powers the Command Center hero) so every surface ends with one
 * consistent "here's your next move" prompt.
 *
 * V1 reuses the existing /api/command-center payload and the rule-based
 * lib/nba engine — no new backend. Option A: navigational verbs only.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, AlertTriangle, Eye, Sparkles, Compass, Wallet, Radar } from "lucide-react";
import { api, type CommandCenterResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { computeNBA, marketPhase, type NBAAction, type NBAKind } from "@/lib/nba";

const KIND_ICON: Record<NBAKind, typeof Eye> = {
  risk: AlertTriangle,
  watchlist: Eye,
  opportunity: Sparkles,
  market: Radar,
  portfolio: Wallet,
  explore: Compass,
};

const SEV_COLOR: Record<string, string> = {
  high: "var(--danger, #fb7185)",
  medium: "var(--warning, #fbbf24)",
  low: "var(--accent, #22d3ee)",
  info: "var(--text-secondary)",
};

/** Presentational card — used by both the page widget and the Command Center hero. */
export function NBACard({ action, hero = false }: { action: NBAAction; hero?: boolean }) {
  const Icon = KIND_ICON[action.kind] ?? Compass;
  const color = SEV_COLOR[action.severity] ?? "var(--accent)";
  return (
    <Link
      href={action.href}
      className="group flex items-center gap-3 rounded-xl transition-colors"
      style={{
        padding: hero ? "16px 18px" : "12px 14px",
        background: "var(--panel, rgba(15,23,42,0.6))",
        border: `1px solid ${color}`,
        boxShadow: hero ? `0 0 24px color-mix(in srgb, ${color} 18%, transparent)` : "none",
        textDecoration: "none",
      }}
    >
      <div
        className="grid place-items-center rounded-lg shrink-0"
        style={{ width: hero ? 40 : 32, height: hero ? 40 : 32, background: `color-mix(in srgb, ${color} 16%, transparent)`, color }}
      >
        <Icon size={hero ? 20 : 16} />
      </div>
      <div className="flex-1 min-w-0">
        {hero && (
          <div style={{ fontSize: "0.6rem", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--text-dim)", marginBottom: 2 }}>
            Start here today
          </div>
        )}
        <div style={{ fontWeight: 650, color: "var(--text-primary)", fontSize: hero ? "1rem" : "0.86rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {action.headline}
        </div>
        {action.detail && (
          <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {action.detail}
          </div>
        )}
      </div>
      <span
        className="inline-flex items-center gap-1.5 shrink-0 rounded-lg font-semibold group-hover:gap-2 transition-all"
        style={{ padding: hero ? "8px 14px" : "6px 11px", fontSize: hero ? "0.82rem" : "0.74rem", background: `color-mix(in srgb, ${color} 16%, transparent)`, color }}
      >
        {action.cta} <ArrowRight size={hero ? 15 : 13} />
      </span>
    </Link>
  );
}

/**
 * Self-fetching next-action widget for pages. Renders the single top action.
 * `context` lightly biases nothing today (kept for future page-aware ranking)
 * but is accepted so callers can pass their page name now.
 */
export default function NextBestAction({
  hero = false,
  className = "",
}: {
  hero?: boolean;
  context?: string;
  className?: string;
}) {
  const { token } = useAuth();
  const [cc, setCc] = useState<CommandCenterResponse | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let live = true;
    api.commandCenter(token ?? undefined)
      .then((d) => { if (live) setCc(d); })
      .catch(() => { /* silent — the card just hides */ })
      .finally(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [token]);

  if (!loaded) return null;
  const phase = marketPhase();
  const actions = computeNBA(cc, {
    ...phase,
    regime: cc?.market_regime ? String(cc.market_regime) : null,
    watchlistCount: cc?.personal_desk_symbols ?? 0,
  });
  const top = actions[0];
  if (!top) return null;

  return (
    <div className={className}>
      <NBACard action={top} hero={hero} />
    </div>
  );
}
