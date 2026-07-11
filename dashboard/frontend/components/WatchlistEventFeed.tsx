"use client";

/**
 * WatchlistEventFeed — turns the watchlist from a static table into an event
 * timeline (Sprint 1, Feature 3). It renders the EXISTING feed already returned
 * by /api/watchlist-operating (`.feed`) — no new detection system. Events also
 * power the Command Center and the Telegram morning brief from this one source.
 */
import Link from "next/link";
import { ArrowRight, TrendingUp, TrendingDown, Target, Crosshair, Activity, Zap, Eye } from "lucide-react";
import type { WatchlistFeedEvent } from "@/lib/api";
import { humanize } from "@/lib/humanize";

function sym(s?: string | null) {
  return String(s || "").replace("NSE:", "").trim().toUpperCase();
}

/** Map an event's type/status to an icon + semantic colour. */
function eventStyle(e: WatchlistFeedEvent): { Icon: typeof Eye; color: string } {
  const t = `${e.type || ""} ${e.setup_status || ""} ${e.headline || ""}`.toLowerCase();
  if (/(breakout|broke|confirmed|entry|demand|ready|trigger)/.test(t)) return { Icon: TrendingUp, color: "var(--success, #34d399)" };
  if (/(stop|sl|invalidat|break down|weak|deteriorat|avoid)/.test(t)) return { Icon: TrendingDown, color: "var(--danger, #fb7185)" };
  if (/(target|took profit|hit)/.test(t)) return { Icon: Target, color: "var(--accent, #22d3ee)" };
  if (/(volume|spike|surge|momentum)/.test(t)) return { Icon: Zap, color: "var(--warning, #fbbf24)" };
  if (/(supply|resistance|approach)/.test(t)) return { Icon: Crosshair, color: "var(--warning, #fbbf24)" };
  return { Icon: Activity, color: "var(--text-secondary)" };
}

function relativeTime(ts?: string): string {
  if (!ts) return "";
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function WatchlistEventFeed({ events }: { events: WatchlistFeedEvent[] }) {
  if (!events || events.length === 0) return null;
  // newest first if timestamps are present
  const ordered = [...events].sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
  return (
    <section>
      <div style={{ fontSize: "0.62rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: 0.5, marginBottom: 8, display: "inline-flex", alignItems: "center", gap: 6 }}>
        <Activity size={11} color="var(--accent)" /> RECENT ACTIVITY ({ordered.length})
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {ordered.slice(0, 20).map((e, i) => {
          const { Icon, color } = eventStyle(e);
          const s = sym(e.symbol);
          const href = s ? `/stock/${s}` : "/watchlist";
          return (
            <Link
              key={`${s}-${e.ts || i}`}
              href={href}
              className="group"
              style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 12px", borderRadius: 11, border: "1px solid rgba(255,255,255,0.06)", background: "var(--panel, rgba(15,23,42,0.5))", textDecoration: "none" }}
            >
              <span className="grid place-items-center rounded-lg shrink-0" style={{ width: 30, height: 30, background: `color-mix(in srgb, ${color} 16%, transparent)`, color }}>
                <Icon size={15} />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: "block", fontSize: "0.86rem", fontWeight: 650, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s && <span style={{ color, marginRight: 6 }}>{s}</span>}
                  {humanize(e.headline || e.type || e.setup_status || "Update")}
                </span>
                {e.setup_status && e.headline && (
                  <span style={{ fontSize: "0.7rem", color: "var(--text-dim)" }}>{humanize(e.setup_status)}</span>
                )}
              </span>
              {e.ts && <span style={{ fontSize: "0.66rem", color: "var(--text-dim)", whiteSpace: "nowrap" }}>{relativeTime(e.ts)}</span>}
              <ArrowRight size={13} className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0" style={{ color }} />
            </Link>
          );
        })}
      </div>
    </section>
  );
}
