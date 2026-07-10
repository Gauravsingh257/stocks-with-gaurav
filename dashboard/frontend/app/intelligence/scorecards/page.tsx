"use client";

import { useCallback, useEffect, useState } from "react";
import { Scorecard, BOOK_LABEL, BOOK_COLOR, fetchScorecards, fmtPct, fmtNum, fmtINR, toneClass } from "@/lib/pil";

const ORDER = ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"];

export default function ScorecardsPage() {
  const [scope, setScope] = useState<"daily" | "monthly">("monthly");
  const [cards, setCards] = useState<Record<string, Scorecard>>({});
  const [period, setPeriod] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (s: "daily" | "monthly") => {
    setLoading(true); setErr(null);
    try {
      const res = await fetchScorecards(s);
      setCards(res.cards); setPeriod(res.period);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(scope); }, [scope, load]);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-[var(--text-secondary)] text-sm">
          Auto-generated engine scorecards · <span className="text-[var(--text-primary)] font-medium">{period || "—"}</span>
        </div>
        <div className="flex gap-1 glass rounded-lg p-1 border border-white/5">
          {(["daily", "monthly"] as const).map((s) => (
            <button key={s} onClick={() => setScope(s)}
              className={`px-3 py-1 text-xs rounded-md capitalize transition-colors ${
                scope === s ? "bg-[var(--accent)]/20 text-[var(--accent)]" : "text-[var(--text-secondary)]"}`}>
              {s}
            </button>
          ))}
        </div>
      </div>

      {err && <div className="glass rounded-xl p-4 border border-rose-500/30 text-sm text-rose-400">{err}</div>}
      {loading && <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Generating scorecards…</div>}

      {!loading && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {ORDER.map((b) => cards[b] && !cards[b].error && <Card key={b} book={b} card={cards[b]} />)}
        </div>
      )}
    </div>
  );
}

function Card({ book, card }: { book: string; card: Scorecard }) {
  const color = BOOK_COLOR[book];
  const f = card.funnel; const p = card.performance; const q = card.quality;
  return (
    <div className="glass rounded-xl p-4 border border-white/5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} />
          <span className="font-semibold text-[var(--text-primary)]">{BOOK_LABEL[book]}</span>
        </div>
        <div className="flex items-center gap-3">
          <QualityBadge label="Engine" value={q.engine_quality_score} />
          <QualityBadge label="Portfolio" value={q.portfolio_quality_score} />
        </div>
      </div>

      {/* Funnel */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        <Funnel n={f.accepted_lifetime} label="Accepted" />
        <Funnel n={f.triggered_lifetime} label="Triggered" />
        <Funnel n={f.closed} label="Closed" sub={`period`} />
        <Funnel n={f.expired} label="Expired" />
      </div>

      {/* Performance row */}
      <div className="grid grid-cols-4 gap-2 mb-3 text-center">
        <Stat label="Hit Rate" value={fmtPct(p.hit_rate_pct, 0)} />
        <Stat label="Expectancy" value={fmtINR(p.expectancy)} tone={p.expectancy} />
        <Stat label="Profit Factor" value={fmtNum(p.profit_factor)} tone={p.profit_factor - 1} />
        <Stat label="Avg Hold" value={`${fmtNum(p.avg_hold_days, 0)}d`} />
      </div>

      {/* Attribution */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[0.72rem] mb-3">
        <Attr label="Best Sector" g={card.attribution.best_sector} good />
        <Attr label="Worst Sector" g={card.attribution.worst_sector} />
        <Attr label="Best Model" g={card.attribution.best_entry_model} good />
        <Attr label="Worst Model" g={card.attribution.worst_entry_model} />
        <Attr label="Best Regime" g={card.attribution.best_regime} good />
        <Attr label="Worst Regime" g={card.attribution.worst_regime} />
      </div>

      {/* Notable */}
      <div className="grid grid-cols-2 gap-3 text-[0.72rem]">
        <div>
          <div className="text-[var(--text-dim)] uppercase text-[0.6rem] mb-1">Top Winners</div>
          {card.notable.top_winners.length ? card.notable.top_winners.map((w) => (
            <div key={w.symbol} className="flex justify-between"><span className="text-[var(--text-secondary)]">{w.symbol}</span><span className="text-emerald-400 tabular-nums">{fmtPct(w.pnl_pct)}</span></div>
          )) : <div className="text-[var(--text-dim)]">—</div>}
        </div>
        <div>
          <div className="text-[var(--text-dim)] uppercase text-[0.6rem] mb-1">Top Losers</div>
          {card.notable.top_losers.length ? card.notable.top_losers.map((w) => (
            <div key={w.symbol} className="flex justify-between"><span className="text-[var(--text-secondary)]">{w.symbol}</span><span className="text-rose-400 tabular-nums">{fmtPct(w.pnl_pct)}</span></div>
          )) : <div className="text-[var(--text-dim)]">—</div>}
        </div>
      </div>

      {/* Quality footer */}
      <div className="flex items-center gap-4 mt-3 pt-2 border-t border-white/5 text-[0.68rem] text-[var(--text-dim)]">
        <span>Ranking Quality: <span className={toneClass(q.ranking_quality)}>{q.ranking_quality === null ? "n/a" : fmtNum(q.ranking_quality)}</span></span>
        <span>Replacement Eff.: <span className="text-[var(--text-secondary)]">{q.replacement_efficiency === null ? "n/a" : fmtPct(q.replacement_efficiency)}</span></span>
        {card.notable.largest_missed_opportunity && (
          <span>Missed: {card.notable.largest_missed_opportunity.symbol} ({fmtPct(card.notable.largest_missed_opportunity.potential_upside_pct)})</span>
        )}
      </div>
    </div>
  );
}

function QualityBadge({ label, value }: { label: string; value: number }) {
  const c = value >= 70 ? "#34d399" : value >= 45 ? "#f59e0b" : "#f43f5e";
  return (
    <div className="text-center">
      <div className="text-[0.55rem] uppercase text-[var(--text-dim)]">{label}</div>
      <div className="text-sm font-bold tabular-nums" style={{ color: c }}>{value.toFixed(0)}</div>
    </div>
  );
}

function Funnel({ n, label, sub }: { n: number; label: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] py-2 text-center">
      <div className="text-lg font-bold text-[var(--text-primary)] tabular-nums">{n}</div>
      <div className="text-[0.58rem] uppercase text-[var(--text-dim)]">{label}</div>
      {sub && <div className="text-[0.5rem] text-[var(--text-dim)]">{sub}</div>}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return (
    <div>
      <div className={`text-sm font-semibold tabular-nums ${tone !== undefined ? toneClass(tone) : "text-[var(--text-primary)]"}`}>{value}</div>
      <div className="text-[0.55rem] uppercase text-[var(--text-dim)]">{label}</div>
    </div>
  );
}

function Attr({ label, g, good }: { label: string; g: { name: string; avg_pnl_pct: number; n: number } | null; good?: boolean }) {
  return (
    <div className="flex justify-between">
      <span className="text-[var(--text-dim)]">{label}</span>
      {g ? (
        <span className={good ? "text-emerald-400" : "text-rose-400"}>
          {g.name} <span className="tabular-nums opacity-70">({fmtPct(g.avg_pnl_pct)})</span>
        </span>
      ) : <span className="text-[var(--text-dim)]">n/a</span>}
    </div>
  );
}
