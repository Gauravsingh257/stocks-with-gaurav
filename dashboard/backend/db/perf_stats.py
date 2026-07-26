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
) -> dict:
    """Canonical metrics for one book of closed trades.

    `trades` must ALREADY be the clean population — callers filter duplicates at
    the query so the rows they display and the rows counted here are identical.
    `duplicates_excluded` is passed through purely so the exclusion is auditable.

    Every returned figure is computed over the same rows. That invariant is the
    whole point of this function: do not add a field derived from a different set.
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

    hit_rate = round(wins / n * 100, 1) if n else 0.0

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
        "book_return_pct": round(sum_pct / slots, 2),
        "book_slots": slots,
        "book_return_basis": f"equal-weight {slots}-slot book (each trade = 1/{slots} of capital)",
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
