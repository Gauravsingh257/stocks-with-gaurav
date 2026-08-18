"""
scripts/backtest_giveback_path.py
=================================
PHASE 3 (read-only) — PATH-BASED counterfactual backtest of a ratcheting
give-back / trailing exit, replayed bar by bar over real daily OHLC.

WHY THIS SUPERSEDES scripts/backtest_giveback.py
------------------------------------------------
The journal-only version reconstructs the trail from `high_since_entry`, i.e.
from the trade's FINAL peak. A real trailing stop ratchets — the trail is
recomputed every time the peak makes a new high. Those are not the same rule:

    a trade that reaches +3%, slips to +2.5%, then runs to +50%
      journal model : trail sits at 0.85 x 50% = +42.5%; final +50% is above it,
                      so the rule "never fired" — the trade keeps its +50%
      real trail    : armed at +3%, stopped at +2.5% — the +50% never happens

So the journal model cannot stop a winner out early, which biases every result
upward and is why its best cell sat on the grid corner at arm 3% / give-back 15%
(a setting that is not a give-back rule at all — it is "scalp at +2.5%"). It also
had to bracket optimistic/pessimistic because the journal does not record whether
the high came before the low.

Replaying the actual bars fixes both: the ratchet is modelled correctly and the
ordering ambiguity collapses to a single, standard, conservative intrabar
assumption.

METHOD
------
For each closed journal trade, fetch daily OHLC from entry date to exit date and
walk it forward:

    peak     = max(peak, bar.high)              -- ratchets up, never down
    armed    = peak_gain_pct >= ARM_PCT
    trail    = entry + (peak - entry) * (1 - GIVEBACK_PCT/100)   [only when armed]

    intrabar order is assumed ADVERSE-FIRST (low before high) — the standard
    conservative choice. Within one bar we therefore check, in order:
        1. original stop-loss  -> exit at stop (or at the open if it gapped below)
        2. trail (if armed)    -> exit at trail (or at the open if it gapped below)
        3. original target     -> exit at target
    Ranking the stop and the trail ahead of the target on the same bar never
    flatters the rule.

    The peak only ratchets on bars where no exit fired, so a trail can never be
    set by a high the position did not live to see.

The counterfactual is compared against the trade's ACTUAL realised P&L. Trades
whose bars cannot be fetched are dropped and reported — never silently defaulted
to "unchanged", which would understate the rule's impact.

CAVEATS THAT REMAIN
-------------------
1. Daily bars only. A trail hit and recovered intraday is treated as an exit.
   That is the correct conservative direction for an exit rule but it does
   overstate how often a trail fires versus the live 2-minute tracker.
2. yfinance daily OHLC is split/dividend adjusted; journal prices are raw. Trades
   whose fetched bar range does not bracket the recorded entry price within
   --price-tolerance are dropped as unreconcilable rather than silently mismatched.
3. Per-trade percentages are averaged, NEVER summed into a "book return" — see
   db/perf_stats.py, the single source of truth for book-level metrics.
4. n < 100 and one market regime. Read the surface for a stable plateau; do not
   pick the single best cell.

USAGE
-----
    python scripts/backtest_giveback_path.py --source api
    python scripts/backtest_giveback_path.py --source api --horizon LONGTERM
    python scripts/backtest_giveback_path.py --source api --arm 10 --giveback 40
    python scripts/backtest_giveback_path.py --source api --cache bars.json

Read-only. Touches no production state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_giveback import (  # noqa: E402
    ARM_GRID,
    GIVEBACK_GRID,
    DEFAULT_API,
    Trade,
    load_trades,
    _median,
)


# ── Bar loading ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float


@dataclass(slots=True)
class PathTrade:
    trade: Trade
    bars: list[Bar] = field(default_factory=list)


def _yf_symbol(symbol: str) -> str:
    return symbol.replace("NSE:", "").strip().upper() + ".NS"


def fetch_bars(trades: list[Trade], rows_by_symbol: dict[str, tuple[str, str]],
               cache_path: str | None, pad_days: int = 3) -> dict[str, list[Bar]]:
    """Daily OHLC per trade key, from yfinance. Cached to JSON when --cache is
    given so repeated sweeps cost no network."""
    cache: dict[str, list[dict]] = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}

    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    from datetime import datetime, timedelta

    out: dict[str, list[Bar]] = {}
    fetched = 0
    for key, (start, end) in rows_by_symbol.items():
        if key in cache:
            out[key] = [Bar(**b) for b in cache[key]]
            continue
        sym = _yf_symbol(key.split("|", 1)[0])
        try:
            # NEVER pad backwards: `start` is the real entry date, and replaying
            # bars from before the position existed stops trades out that were
            # never open. Forward padding is harmless.
            s = datetime.fromisoformat(start[:10])
            e = datetime.fromisoformat(end[:10]) + timedelta(days=pad_days)
            df = yf.Ticker(sym).history(start=s.date().isoformat(),
                                        end=e.date().isoformat())
            if df is None or df.empty:
                continue
            df = df.dropna()
            bars = [
                Bar(date=str(idx)[:10], open=float(r["Open"]), high=float(r["High"]),
                    low=float(r["Low"]), close=float(r["Close"]))
                for idx, r in df.iterrows()
            ]
            if not bars:
                continue
            out[key] = bars
            cache[key] = [b.__dict__ if hasattr(b, "__dict__")
                          else {k: getattr(b, k) for k in Bar.__slots__} for b in bars]
            fetched += 1
        except Exception as exc:
            print(f"  bar fetch failed {sym}: {exc}", file=sys.stderr)

    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except Exception as exc:
            print(f"  cache write failed: {exc}", file=sys.stderr)
    if fetched:
        print(f"  fetched {fetched} symbol windows from yfinance")
    return out


# ── The simulator ────────────────────────────────────────────────────────────

def simulate(t: Trade, bars: list[Bar], stop_loss: float, target: float | None,
             arm_pct: float, giveback_pct: float, slippage_pp: float,
             fill_model: str = "close") -> tuple[float, str]:
    """Replay the bars under the ratcheting trail. Returns (pnl_pct, exit_tag).

    fill_model decides what price the exit rules are tested against, and this
    matters more than any parameter in the sweep:

      "extremes"  test against the bar's high/low, adverse-first (stop, trail,
                  target). This is the textbook backtest, and it is WRONG for
                  this system: the live tracker polls CMP on a 2-minute cadence
                  and compares `cmp <= sl`, so it never sees a one-tick intraday
                  spike through a level. Replaying extremes therefore exits
                  trades the live engine would have held.

      "close"     test against the daily close, which is what the tracker's last
                  sample of the day effectively observes. Understates intraday
                  triggers slightly, but reproduces the system's ACTUAL closed
                  outcomes far better — which the CONTROL run measures directly.

    The peak also ratchets on the chosen series, so a trail can never be set by
    a high the tracker never sampled.
    """
    entry = t.entry
    peak = entry
    armed = False
    use_extremes = fill_model == "extremes"

    def pct(px: float) -> float:
        return (px - entry) / entry * 100.0

    for bar in bars:
        low = bar.low if use_extremes else bar.close
        high = bar.high if use_extremes else bar.close

        trail = None
        if armed:
            trail = entry + (peak - entry) * (1.0 - giveback_pct / 100.0)

        # 1. original stop
        if stop_loss > 0 and low <= stop_loss:
            fill = min(stop_loss, bar.open) if use_extremes and bar.open < stop_loss else \
                (stop_loss if use_extremes else bar.close)
            return round(pct(fill), 2), "STOP"

        # 2. ratcheting trail
        if trail is not None and low <= trail:
            fill = min(trail, bar.open) if use_extremes and bar.open < trail else \
                (trail if use_extremes else bar.close)
            return round(pct(fill) - slippage_pp, 2), "TRAIL"

        # 3. original target
        if target and high >= target:
            fill = target if use_extremes else bar.close
            return round(pct(fill), 2), "TARGET"

        # survived the bar — now the peak may ratchet
        if high > peak:
            peak = high
            if pct(peak) >= arm_pct:
                armed = True

    # Never exited within the window: fall back to the trade's real result, so a
    # short bar window can never invent a better outcome than actually occurred.
    return t.actual_pct, "ACTUAL"


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class PathResult:
    n: int
    mean_pct: float
    median_pct: float
    win_rate: float
    profit_factor: float
    worst: float
    trail_exits: int
    winners_hurt: int
    winners_hurt_pp: float
    losers_saved: int
    losers_saved_pp: float


def evaluate_path(paths: list[PathTrade], stops: dict[int, tuple[float, float | None]],
                  arm_pct: float, giveback_pct: float, slippage_pp: float,
                  fill_model: str = "close") -> PathResult:
    pnls: list[float] = []
    trail_exits = winners_hurt = losers_saved = 0
    winners_hurt_pp = losers_saved_pp = 0.0

    for i, p in enumerate(paths):
        sl, tgt = stops[i]
        new_pct, tag = simulate(p.trade, p.bars, sl, tgt, arm_pct, giveback_pct,
                                slippage_pp, fill_model)
        pnls.append(new_pct)
        if tag == "TRAIL":
            trail_exits += 1
            delta = new_pct - p.trade.actual_pct
            if p.trade.actual_pct > 0 and delta < 0:
                winners_hurt += 1
                winners_hurt_pp += delta
            elif delta > 0:
                losers_saved += 1
                losers_saved_pp += delta

    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gl = abs(sum(losses))
    return PathResult(
        n=len(pnls),
        mean_pct=round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        median_pct=round(_median(pnls), 2),
        win_rate=round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        profit_factor=round(sum(wins) / gl, 2) if gl > 0 else float("inf"),
        worst=round(min(pnls), 2) if pnls else 0.0,
        trail_exits=trail_exits,
        winners_hurt=winners_hurt,
        winners_hurt_pp=round(winners_hurt_pp, 1),
        losers_saved=losers_saved,
        losers_saved_pp=round(losers_saved_pp, 1),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["api", "db", "file"], default="api")
    ap.add_argument("--api", default=os.getenv("DASHBOARD_URL", DEFAULT_API))
    ap.add_argument("--db", default="dashboard.db")
    ap.add_argument("--file", default="journal.json")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--horizon", default=None)
    ap.add_argument("--slippage-pp", type=float, default=0.5)
    ap.add_argument("--price-tolerance", type=float, default=25.0,
                    help="max %% gap between journal entry price and the fetched "
                         "bar range before a trade is dropped as unreconcilable")
    ap.add_argument("--cache", default=None, help="JSON path to cache fetched bars")
    ap.add_argument("--fill-model", choices=["close", "extremes", "both"], default="both",
                    help="price series the exit rules are tested against; the CONTROL "
                         "run tells you which one actually reproduces reality")
    ap.add_argument("--arm", type=float, default=None)
    ap.add_argument("--giveback", type=float, default=None)
    args = ap.parse_args()

    # Reuse the journal loader, then re-read the raw rows for SL/target/dates.
    trades, skips = load_trades(args)
    if not trades:
        print("No usable trades.", file=sys.stderr)
        return 1

    if args.source == "api":
        from scripts.backtest_giveback import _rows_from_api
        raw = _rows_from_api(args.api, args.limit)
    elif args.source == "db":
        from scripts.backtest_giveback import _rows_from_db
        raw = _rows_from_db(args.db)
    else:
        from scripts.backtest_giveback import _rows_from_file
        raw = _rows_from_file(args.file)

    by_key: dict[str, dict] = {}
    for r in raw:
        if r.get("is_duplicate"):
            continue
        key = f"{r.get('symbol')}|{r.get('created_at')}"
        by_key[key] = r

    # ENTRY DATE, not creation date. `created_at` is when the idea was ARMED
    # (PENDING); under arm-on-tap a position can sit pending for days before its
    # entry is genuinely traded through. Replaying from created_at walks bars the
    # position never lived through and stops it out before it existed. The
    # journal has no entered_at column, so reconstruct it from the two fields it
    # does have: entry_date = closed_at - days_held.
    from datetime import datetime, timedelta

    windows: dict[str, tuple[str, str]] = {}
    undated = 0
    for key, r in by_key.items():
        end = str(r.get("closed_at") or "")[:10]
        if not end:
            undated += 1
            continue
        try:
            closed = datetime.fromisoformat(end)
            start = (closed - timedelta(days=int(r.get("days_held") or 0))).date().isoformat()
        except Exception:
            undated += 1
            continue
        windows[key] = (start, end)
    if undated:
        print(f"  {undated} rows had no usable date window")

    print("Fetching daily bars for each trade window...")
    bars_by_key = fetch_bars(trades, windows, args.cache)

    paths: list[PathTrade] = []
    stops: dict[int, tuple[float, float | None]] = {}
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for t in trades:
        key = next((k for k, r in by_key.items()
                    if r.get("symbol") == t.symbol
                    and abs(float(r.get("entry_price") or 0) - t.entry) < 1e-6), None)
        if key is None or key not in bars_by_key:
            drop("no_bars")
            continue
        bars = bars_by_key[key]
        lo = min(b.low for b in bars)
        hi = max(b.high for b in bars)
        # Adjusted-vs-raw price reconciliation.
        if not (lo * (1 - args.price_tolerance / 100) <= t.entry <= hi * (1 + args.price_tolerance / 100)):
            drop("price_unreconcilable")
            continue
        r = by_key[key]
        try:
            sl = float(r.get("stop_loss") or 0)
        except (TypeError, ValueError):
            sl = 0.0
        tgt = r.get("target_1")
        try:
            tgt = float(tgt) if tgt else None
        except (TypeError, ValueError):
            tgt = None
        stops[len(paths)] = (sl, tgt)
        paths.append(PathTrade(trade=t, bars=bars))

    if not paths:
        print("No trades survived bar reconciliation.", file=sys.stderr)
        return 1

    actual = [p.trade.actual_pct for p in paths]
    wins = [x for x in actual if x > 0]
    losses = [x for x in actual if x <= 0]
    gl = abs(sum(losses))
    print()
    print("=" * 86)
    print("BASELINE — actual results, restricted to trades with reconcilable bars")
    print("=" * 86)
    if skips:
        print("journal filter:", ", ".join(f"{k}={v}" for k, v in sorted(skips.items())))
    if dropped:
        print("bar reconciliation dropped:", ", ".join(f"{k}={v}" for k, v in sorted(dropped.items())))
    print(f"trades        : {len(paths)} of {len(trades)} journal rows")
    base_mean = sum(actual) / len(actual)
    base_pf = (sum(wins) / gl) if gl > 0 else float("inf")
    print(f"mean P&L      : {base_mean:+.2f}%   median {_median(actual):+.2f}%")
    print(f"win rate      : {len(wins)/len(actual)*100:.1f}%")
    print(f"profit factor : {base_pf:.2f}")
    print(f"worst trade   : {min(actual):+.2f}%")
    print()

    # ── CONTROL: replay with the trail switched off ──────────────────────────
    # Non-negotiable validation. With no trail, the simulator replays only the
    # original stop and target, so it must land close to the trades' ACTUAL
    # results. If it does not, the replay itself is wrong (bad dates, adjusted
    # vs raw prices, missing bars) and every number in the sweep below is
    # measuring the simulator's error rather than the rule's effect.
    models = ["close", "extremes"] if args.fill_model == "both" else [args.fill_model]
    tol = 1.0
    print("=" * 86)
    print("CONTROL — same replay, trail DISABLED (must reproduce the baseline)")
    print("=" * 86)
    passed: list[str] = []
    for fm in models:
        c = evaluate_path(paths, stops, arm_pct=1e9, giveback_pct=0.0,
                          slippage_pp=0.0, fill_model=fm)
        drift = c.mean_pct - base_mean
        ok = abs(drift) <= tol
        passed.append(fm) if ok else None
        print(f"  [{fm:<8}] mean {base_mean:+.2f}% -> {c.mean_pct:+.2f}%  "
              f"drift {drift:+6.2f}pp   PF {base_pf:.2f} -> {c.profit_factor:.2f}   "
              f"{'PASS' if ok else 'FAIL'}")
    print()
    if not passed:
        print("  *** CONTROL FAILED for every fill model.")
        print("  The bar replay does not reproduce known outcomes, so the sweep")
        print("  below would measure simulator error, not the give-back rule.")
        print("  Do NOT draw conclusions from the grids until this passes.")
        print()
    else:
        print(f"  interpretable fill model(s): {', '.join(passed)}")
        print("  Grids for FAILING models are printed for diagnosis only.")
        print()

    header = "arm\\giveback |" + "".join(f"{g:>8.0f}%" for g in GIVEBACK_GRID)

    def grid(title: str, cell, fm: str) -> None:
        print("=" * len(header))
        print(title)
        print("=" * len(header))
        print(header)
        print("-" * len(header))
        for arm in ARM_GRID:
            cells = [cell(evaluate_path(paths, stops, arm, gb, args.slippage_pp, fm))
                     for gb in GIVEBACK_GRID]
            print(f"{arm:>11.0f}% |" + "".join(cells))
        print()

    for fm in models:
        flag = "INTERPRETABLE" if fm in passed else "CONTROL FAILED — DIAGNOSTIC ONLY"
        print("#" * len(header))
        print(f"#  PATH SIMULATION — fill model '{fm}'  [{flag}]  "
              f"slippage {args.slippage_pp:.1f}pp")
        print("#" * len(header))
        grid(f"mean P&L% per trade (baseline {base_mean:+.2f}%)",
             lambda r: f"{r.mean_pct:>+9.2f}", fm)
        grid(f"profit factor (baseline {base_pf:.2f})",
             lambda r: f"{r.profit_factor:>9.2f}" if r.profit_factor != float("inf")
             else f"{'inf':>9}", fm)
        grid("winners hurt — count / pp surrendered  (the bar: ZERO)",
             lambda r: f"{r.winners_hurt:>3}/{abs(r.winners_hurt_pp):>5.1f}", fm)
        grid("trail exits (how often the rule actually fired)",
             lambda r: f"{r.trail_exits:>9}", fm)

    if args.arm is not None and args.giveback is not None:
        r = evaluate_path(paths, stops, args.arm, args.giveback, args.slippage_pp)
        print("=" * 86)
        print(f"DETAIL — arm {args.arm:.0f}%, give-back {args.giveback:.0f}% of peak")
        print("=" * 86)
        print(f"mean {base_mean:+.2f}% -> {r.mean_pct:+.2f}%   "
              f"PF {base_pf:.2f} -> {r.profit_factor:.2f}   "
              f"worst {min(actual):+.2f}% -> {r.worst:+.2f}%")
        print(f"trail exits {r.trail_exits}  |  winners hurt {r.winners_hurt} "
              f"({r.winners_hurt_pp:+.1f}pp)  |  losers improved {r.losers_saved} "
              f"({r.losers_saved_pp:+.1f}pp)")
        print()
        print(f"  {'symbol':<13}{'horizon':<10}{'actual%':>9}{'ruled%':>9}{'delta':>9}  exit")
        for i, p in enumerate(paths):
            sl, tgt = stops[i]
            new_pct, tag = simulate(p.trade, p.bars, sl, tgt, args.arm,
                                    args.giveback, args.slippage_pp)
            if tag == "TRAIL":
                print(f"  {p.trade.symbol.replace('NSE:',''):<13}{p.trade.horizon:<10}"
                      f"{p.trade.actual_pct:>9.2f}{new_pct:>9.2f}"
                      f"{new_pct - p.trade.actual_pct:>+9.2f}  {tag}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
