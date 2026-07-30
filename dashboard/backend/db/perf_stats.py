"""Canonical performance-statistics engine — the ONE place book metrics are defined.

Why this module exists
──────────────────────
The Swing Portfolio header once published "45.7% win rate · 70 completed · Total
return: -41.37%". Every part of that was wrong, because each surface computed its
own numbers its own way:

  * the win rate was taken over a setup-collapsed population (46 rows) while the
    return summed ALL 70 rows — two figures in one sentence describing different
    trade sets (the clean set actually summed to +23.51%);
  * "Total return" was `SUM(profit_loss_pct)`, which adds percentages taken on
    different capital bases and overstated the real 20-slot book move ~20-fold;
  * 23 of those rows were re-seed artifacts — one held position journaled over
    and over (CIPLA 11 times inside 51 minutes), not separate trades.

Any surface that reimplements these formulas will drift from the others again.
So every book — Swing, Long-Term, Momentum — routes through `compute_book_stats`
here, and the payload carries an explicit description of the population and the
definitions used, so two surfaces can never quietly disagree about what they mean.

The definitions (single source of truth)
────────────────────────────────────────
  population        real closed trades only; re-seed duplicates excluded
  win               a trade whose net return is > 0 (NOT "hit target" — a trade
                    cut early for +2% is a win; see WIN_DEFINITION)
  sum_trade_return  Σ per-trade % — a diagnostic, NEVER a portfolio return
  book_return       Σ per-trade % ÷ slots — an N-slot equal-weight book puts 1/N
                    of capital behind each position, so a trade returning p%
                    moves the book by p/N. THIS is what "return" means on a book.

`research_track_record` is deliberately NOT routed through here: it measures a
different population (every published idea, including EXPIRED ones that were
never traded) under a different win definition (target reached). It declares
that difference explicitly via its own `population` / `win_definition` keys so
the mismatch with a portfolio book is self-evident rather than confusing.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# Published contract strings — surfaces render these instead of inventing prose,
# so the disclosed basis can never drift from the arithmetic above it.
WIN_DEFINITION = "net return > 0 after costs of the exit (target, trail, or cut early)"
RETURN_DEFINITION = (
    "equal-weight book return: each position is 1/slots of capital, so the book "
    "moves by the sum of trade returns divided by slots"
)
POPULATION_CLOSED_BOOK = "closed positions actually held in this book (re-seed duplicates excluded)"

# How many recently-banked winners the header surfaces.
RECENT_BANKED_LIMIT = 3

# Exit reasons treated as "cut early" — neither a target nor a stop.
_TARGET = "TARGET_HIT"
_STOP = "STOP_HIT"


def _f(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compute_book_stats(
    trades: Iterable[Mapping[str, Any]],
    *,
    slots: int,
    book: str,
    duplicates_excluded: int = 0,
    population: str = POPULATION_CLOSED_BOOK,
    open_positions: Iterable[Mapping[str, Any]] | None = None,
) -> dict:
    """Canonical metrics for one book: realised (closed) + open (mark-to-market).

    `trades` must ALREADY be the clean population — callers filter duplicates at
    the query so the rows they display and the rows counted here are identical.
    `duplicates_excluded` is passed through purely so the exclusion is auditable.

    `open_positions` are the ACTIVE (genuinely entered) rows. Their unrealised
    P&L is what makes the book return move with the market instead of only when
    something closes — a book holding 19 green positions is not flat. PENDING
    rows are excluded by the caller: they are armed, not entered, so they carry
    no P&L.

    Two things stay deliberately separate:
      * RETURN blends realised + unrealised, because that is what a portfolio
        return means — `total_book_return_pct`.
      * WIN RATE stays realised-only (`hit_rate_pct`). An open position at +0.3%
        has not won anything yet; folding live marks into a win rate makes it
        swing with the tape and quietly overstates the record. The open book is
        reported alongside as counts (`open_winners`/`open_losers`) and a
        clearly-named `blended_hit_rate_pct` for anyone who wants it — but the
        headline rate must remain the realised one.

    Every closed-trade figure is computed over the same rows. That invariant is
    the point of this function: do not add a field derived from a different set.
    """
    rows = [t for t in trades]
    n = len(rows)
    pnls = [_f(t.get("profit_loss_pct")) for t in rows]

    wins = sum(1 for p in pnls if p > 0)
    losses = n - wins
    sum_pct = round(sum(pnls), 2)
    slots = int(slots) if slots and int(slots) > 0 else 1

    def _reason(t: Mapping[str, Any]) -> str:
        return str(t.get("exit_reason") or "").strip().upper()

    target_hits = sum(1 for t in rows if _reason(t) == _TARGET)
    stop_hits = sum(1 for t in rows if _reason(t) == _STOP)
    structure_exits = sum(1 for t in rows if _reason(t) == "STRUCTURE_BREAK")
    other_exits = n - target_hits - stop_hits - structure_exits
    # What the UI calls "cut early": everything that was neither target nor stop.
    cut_early = structure_exits + other_exits

    r_vals = [_f(t.get("r_multiple")) for t in rows if t.get("r_multiple") is not None]
    days = [_f(t.get("days_held")) for t in rows]

    # ── Recently banked wins ─────────────────────────────────────────────────
    # Once a position exits, its P&L is frozen and it leaves the open book, so a
    # big win stops being visible anywhere except as a small nudge inside an
    # aggregate — banking SCANSTL at +51.18% moved the realised book by only
    # +2.56pp and the blended win rate not at all. Surfacing the actual closes
    # makes realised outcomes legible instead of buried.
    banked = sorted(
        ({"symbol": str(t.get("symbol") or "").replace("NSE:", ""),
          "pnl_pct": round(_f(t.get("profit_loss_pct")), 2),
          "closed_at": str(t.get("closed_at") or "")[:10],
          "exit_reason": str(t.get("exit_reason") or "")}
         for t in rows if _f(t.get("profit_loss_pct")) > 0 and t.get("closed_at")),
        key=lambda x: x["closed_at"], reverse=True,
    )[:RECENT_BANKED_LIMIT]

    hit_rate = round(wins / n * 100, 1) if n else 0.0

    # ── Open book (mark-to-market) ───────────────────────────────────────────
    opens = [p for p in (open_positions or [])]
    open_pnls = [_f(p.get("profit_loss_pct")) for p in opens]
    open_n = len(opens)
    open_sum = round(sum(open_pnls), 2)
    open_win = sum(1 for p in open_pnls if p > 0)
    realized_book = round(sum_pct / slots, 2)
    unrealized_book = round(open_sum / slots, 2)
    total_book = round(realized_book + unrealized_book, 2)
    blended_n = n + open_n

    return {
        "book": book,
        # ── the published basis, carried with the numbers ──
        "population": population,
        "win_definition": WIN_DEFINITION,
        "return_definition": RETURN_DEFINITION,
        "duplicates_excluded": int(duplicates_excluded),
        # ── counts (one population) ──
        "total_trades": n,
        "wins": wins,
        "losses": losses,
        "hit_rate_pct": hit_rate,
        # ── exit attribution (sums to total_trades) ──
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "structure_exits": structure_exits,
        "other_exits": other_exits,
        "cut_early": cut_early,
        "target_hit_rate_pct": round(target_hits / n * 100, 1) if n else 0.0,
        # ── returns: the two quantities, named for what they are ──
        "avg_trade_return_pct": round(sum(pnls) / n, 2) if n else 0.0,
        "sum_trade_return_pct": sum_pct,
        "book_slots": slots,
        "book_return_basis": f"equal-weight {slots}-slot book (each trade = 1/{slots} of capital)",
        # ── Return: realised + open, explicitly named ────────────────────────
        # Render `total_book_return_pct` as THE return — it moves with the market
        # because it marks the open book. The two components are exposed so the
        # split is visible rather than implied.
        "realized_book_return_pct": realized_book,
        "unrealized_book_return_pct": unrealized_book,
        "total_book_return_pct": total_book,
        # ── Open book ────────────────────────────────────────────────────────
        "open_positions": open_n,
        "open_winners": open_win,
        "open_losers": open_n - open_win,
        "open_sum_pct": open_sum,
        "open_avg_pct": round(open_sum / open_n, 2) if open_n else 0.0,
        # Closed wins + currently-green opens over everything entered. Disclosed
        # for completeness; `hit_rate_pct` (realised) stays the headline because
        # an open position has not won until it closes.
        "blended_hit_rate_pct": round((wins + open_win) / blended_n * 100, 1) if blended_n else 0.0,
        "blended_trades": blended_n,
        # Most recent closed winners — a banked result the header can point at.
        "recent_banked": banked,
        # `book_return_pct` predates the split and means REALISED only. Kept for
        # older consumers; prefer the explicit names above.
        "book_return_pct": realized_book,
        "best_pnl_pct": round(max(pnls), 2) if pnls else 0.0,
        "worst_pnl_pct": round(min(pnls), 2) if pnls else 0.0,
        "avg_days_held": round(sum(days) / n, 1) if n else 0.0,
        "expectancy_r": round(sum(r_vals) / len(r_vals), 3) if r_vals else None,
        # ── deprecated aliases ────────────────────────────────────────────────
        # `avg_pnl_pct` / `total_pnl_pct` predate the rename. `total_pnl_pct` is a
        # SUM of percentages, never a return — kept only so older consumers keep
        # working. The unique_* keys once carried a SECOND, divergent population,
        # which is exactly what produced the misleading header; they now mirror
        # the single basis so nothing can mix the two again.
        "avg_pnl_pct": round(sum(pnls) / n, 2) if n else 0.0,
        "total_pnl_pct": sum_pct,
        "unique_trades": n,
        "unique_wins": wins,
        "unique_hit_rate_pct": hit_rate,
        "repeat_reentries_collapsed": 0,
    }
