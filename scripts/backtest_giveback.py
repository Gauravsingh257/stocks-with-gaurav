"""
scripts/backtest_giveback.py
============================
PHASE 3 (read-only) — counterfactual backtest of a GIVE-BACK / trailing exit rule
against the closed-trade history in `portfolio_journal`.

WHY THIS EXISTS
---------------
The 2026-08 selection audit (docs/PORTFOLIO_SELECTION_AUDIT_2026-08.md) found that
nothing in the tracking engine reacts to a winner turning into a loser: the exit
vocabulary is fixed-target / fixed-SL / trend-break / structure-break / stale-exit.
Measured give-back (peak unrealised gain minus final P&L) averaged 11.9pp on the
long-term book. This script measures whether a give-back rule would actually have
helped — BEFORE any flag is added, per the validation-phase rule.

THE RULE BEING TESTED
---------------------
    arm      : once unrealised gain reaches ARM_PCT, start trailing
    give-back: exit if the position surrenders GIVEBACK_PCT of its PEAK gain

    exit_level_pct = MFE_pct * (1 - GIVEBACK_PCT/100)

METHOD — and the bracket it requires
------------------------------------
Each journal row carries `high_since_entry` (peak), `low_since_entry` (trough) and
the realised exit. What it does NOT carry is the ORDER of the high and the low.
That ambiguity is the whole methodological problem, and it is handled by bracketing
rather than by picking a convenient assumption:

  OPTIMISTIC  rule fires <=> MFE >= ARM  AND  final < exit_level
      If the trade ENDED below the trail level while peaking above it, price must
      have crossed that level after the peak — certain, no assumption needed. But
      this counts a trade that dipped through the trail and then RECOVERED as
      untouched, which systematically UNDERSTATES winners hurt. It is the
      lower bound on the rule's damage.

  PESSIMISTIC rule fires <=> MFE >= ARM  AND  trough < exit_level
      Assumes every dip below the trail happened after the peak, so every
      recovery-after-dip is cut short. This OVERSTATES damage. It is the upper
      bound.

The truth is between the two. A rule that only looks good in the optimistic view
is not evidence — it is an artifact of the missing ordering. Only a band that
holds up in BOTH views justifies a flag.

The counterfactual exit is `exit_level_pct` minus a slippage allowance. Trades
where the rule does not fire keep their actual result, unchanged.

FIDELITY CAVEATS (stated, not hidden)
-------------------------------------
1. `high_since_entry` is sampled by the tracker at its poll cadence (~2 min in
   market hours, 15 min outside), so it is a SAMPLED-CLOSE proxy for the true
   intraday MFE and understates it. This is a feature here, not a bug: the live
   rule would read the exact same sampled series, so the backtest is faithful to
   what is actually executable.
2. Gap risk: a position can gap below the trail level overnight. `--slippage-pp`
   models this crudely; the sweep is re-run at a pessimistic value to check the
   conclusion is not slippage-fragile.
3. Per-trade percentages are averaged, NEVER summed into a "book return" — see
   db/perf_stats.py, the single source of truth for book-level metrics.
4. Sample is small (n<100) and dominated by one market regime. Treat the output
   as evidence for a decision, not as a tuned parameter set. Do not pick the grid
   cell with the highest mean — read the whole surface for a stable plateau.

USAGE
-----
    python scripts/backtest_giveback.py --source api
    python scripts/backtest_giveback.py --source db --db dashboard.db
    python scripts/backtest_giveback.py --source file --file journal.json
    python scripts/backtest_giveback.py --source api --horizon LONGTERM --slippage-pp 1.0

Read-only. Touches no production state and writes no files unless --out is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

DEFAULT_API = "https://web-production-2781a.up.railway.app"

# Grid swept by default. Deliberately coarse — this is a decision aid, not a
# parameter optimiser, and a fine grid on n<100 trades is curve-fitting.
# Extended below the first run's best cell (arm 5% / give-back 25%) because that
# sat on the grid corner — an optimum at the edge means the real optimum is
# outside the grid, and you cannot tell a genuine plateau from a monotone slope
# without looking past it.
ARM_GRID = [3.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]
GIVEBACK_GRID = [15.0, 20.0, 25.0, 33.0, 40.0, 50.0, 60.0, 75.0]


# ── Data loading ─────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Trade:
    symbol: str
    horizon: str
    entry: float
    exit: float
    peak: float
    trough: float
    actual_pct: float
    days_held: int
    exit_reason: str

    @property
    def mfe_pct(self) -> float:
        """Peak unrealised gain, in % of entry. Floored at 0: a trade whose
        tracker never saw a tick above entry has no peak to trail from."""
        if self.entry <= 0:
            return 0.0
        return max(0.0, (self.peak - self.entry) / self.entry * 100.0)

    @property
    def mae_pct(self) -> float:
        """Deepest unrealised loss, in % of entry (<= 0). Used only by the
        pessimistic bound — see the module docstring."""
        if self.entry <= 0:
            return 0.0
        return (self.trough - self.entry) / self.entry * 100.0

    @property
    def giveback_pp(self) -> float:
        return self.mfe_pct - self.actual_pct


def _rows_from_api(base: str, limit: int) -> list[dict]:
    """Prefer `requests` — it ships its own certifi CA bundle, whereas a stale
    system store makes urllib fail the handshake on some dev machines. urllib is
    the fallback so the script keeps working with no third-party deps."""
    # The route caps limit at 200 (Query(..., le=200)); asking for more is a 422.
    url = f"{base.rstrip('/')}/api/portfolio/journal/all?limit={min(limit, 200)}"
    try:
        import requests

        resp = requests.get(url, timeout=90)
        resp.raise_for_status()
        payload = resp.json()
    except ImportError:
        import urllib.request

        with urllib.request.urlopen(url, timeout=90) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("items", payload if isinstance(payload, list) else [])


def _rows_from_db(path: str) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_journal)")}
        where = "WHERE COALESCE(is_duplicate, 0) = 0" if "is_duplicate" in cols else ""
        return [dict(r) for r in conn.execute(f"SELECT * FROM portfolio_journal {where}")]
    finally:
        conn.close()


def _rows_from_file(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("items", payload) if isinstance(payload, dict) else payload


def load_trades(args) -> tuple[list[Trade], dict[str, int]]:
    """Returns (trades, skip_reasons). Excludes duplicate-flagged rows and rows
    without the fields the rule needs — and reports exactly what was dropped, so
    a shrinking sample can never pass unnoticed."""
    if args.source == "api":
        rows = _rows_from_api(args.api, args.limit)
    elif args.source == "db":
        rows = _rows_from_db(args.db)
    else:
        rows = _rows_from_file(args.file)

    skips: dict[str, int] = {}

    def skip(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    trades: list[Trade] = []
    for r in rows:
        if r.get("is_duplicate"):
            skip("duplicate_flagged")
            continue
        try:
            entry = float(r["entry_price"])
            peak = float(r["high_since_entry"])
            exit_px = float(r["exit_price"])
        except (KeyError, TypeError, ValueError):
            skip("missing_price_fields")
            continue
        # Trough is only needed by the pessimistic bound; fall back to the exit
        # price (never worse than actually observed) when the tracker has none.
        try:
            trough = float(r["low_since_entry"])
        except (KeyError, TypeError, ValueError):
            trough = exit_px
        if entry <= 0:
            skip("bad_entry")
            continue
        horizon = str(r.get("horizon") or "?").upper()
        if args.horizon and horizon != args.horizon.upper():
            skip(f"horizon!={args.horizon.upper()}")
            continue
        actual = r.get("profit_loss_pct")
        actual_pct = float(actual) if actual is not None else (exit_px - entry) / entry * 100.0
        trades.append(Trade(
            symbol=str(r.get("symbol") or "?"),
            horizon=horizon,
            entry=entry,
            exit=exit_px,
            peak=peak,
            trough=trough,
            actual_pct=round(actual_pct, 2),
            days_held=int(r.get("days_held") or 0),
            exit_reason=str(r.get("exit_reason") or "?"),
        ))
    return trades, skips


# ── The rule ─────────────────────────────────────────────────────────────────

def apply_rule(t: Trade, arm_pct: float, giveback_pct: float,
               slippage_pp: float, mode: str = "optimistic") -> tuple[float, bool]:
    """Counterfactual P&L% for one trade under the give-back rule.

    Returns (pnl_pct, rule_fired). The trade must first reach the arm level.
    Whether the trail then fired depends on ordering the journal does not
    record, so `mode` selects which side of the bracket to compute:

      optimistic — fires only if the trade FINISHED below the trail. Certain,
                   but blind to dip-then-recover trades. Lower bound on damage.
      pessimistic — fires if the trade ever TRADED below the trail, assuming
                   that dip came after the peak. Upper bound on damage.
    """
    mfe = t.mfe_pct
    if mfe < arm_pct:
        return t.actual_pct, False
    exit_level = mfe * (1.0 - giveback_pct / 100.0)
    breached = t.mae_pct < exit_level if mode == "pessimistic" else t.actual_pct < exit_level
    if not breached:
        return t.actual_pct, False
    # Exiting at the trail can only ever be an improvement over a WORSE actual
    # result; if the trade finished better, the rule cut it short. Both cases
    # are just "you exited here" — the sign of the delta is what we measure.
    return round(exit_level - slippage_pp, 2), True


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Result:
    n: int
    mean_pct: float
    median_pct: float
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    worst: float
    fired: int
    winners_hurt: int          # profitable trades the rule cut EARLIER (lower P&L)
    winners_hurt_pp: float     # total P&L given up on those
    losers_saved: int          # losing trades the rule turned into a better outcome
    losers_saved_pp: float


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def evaluate(trades: list[Trade], arm_pct: float, giveback_pct: float,
             slippage_pp: float, mode: str = "optimistic") -> Result:
    pnls: list[float] = []
    fired = winners_hurt = losers_saved = 0
    winners_hurt_pp = losers_saved_pp = 0.0

    for t in trades:
        new_pct, did_fire = apply_rule(t, arm_pct, giveback_pct, slippage_pp, mode)
        pnls.append(new_pct)
        if did_fire:
            fired += 1
            delta = new_pct - t.actual_pct
            # A trade that WAS profitable and is now less profitable = damage.
            if t.actual_pct > 0 and delta < 0:
                winners_hurt += 1
                winners_hurt_pp += delta
            elif delta > 0:
                losers_saved += 1
                losers_saved_pp += delta

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return Result(
        n=len(pnls),
        mean_pct=round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        median_pct=round(_median(pnls), 2),
        win_rate=round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
        avg_loss=round(sum(losses) / len(losses), 2) if losses else 0.0,
        worst=round(min(pnls), 2) if pnls else 0.0,
        fired=fired,
        winners_hurt=winners_hurt,
        winners_hurt_pp=round(winners_hurt_pp, 1),
        losers_saved=losers_saved,
        losers_saved_pp=round(losers_saved_pp, 1),
    )


# ── Reporting ────────────────────────────────────────────────────────────────

def print_baseline(trades: list[Trade], skips: dict[str, int]) -> Result:
    base = evaluate(trades, arm_pct=1e9, giveback_pct=0.0, slippage_pp=0.0)
    print("=" * 78)
    print("BASELINE — actual closed trades, no rule applied")
    print("=" * 78)
    if skips:
        print("excluded rows:", ", ".join(f"{k}={v}" for k, v in sorted(skips.items())))
    horizons: dict[str, int] = {}
    for t in trades:
        horizons[t.horizon] = horizons.get(t.horizon, 0) + 1
    print(f"trades         : {base.n}  ({', '.join(f'{k} {v}' for k, v in sorted(horizons.items()))})")
    print(f"mean P&L       : {base.mean_pct:+.2f}%   median {base.median_pct:+.2f}%")
    print(f"win rate       : {base.win_rate:.1f}%")
    print(f"profit factor  : {base.profit_factor}")
    print(f"avg win / loss : {base.avg_win:+.2f}% / {base.avg_loss:+.2f}%")
    print(f"worst trade    : {base.worst:+.2f}%")
    gb = [t.giveback_pp for t in trades]
    print(f"give-back (MFE-final): median {_median(gb):.1f}pp  mean {sum(gb)/len(gb):.1f}pp"
          if gb else "")
    print()
    print("Worst give-backs in the sample:")
    for t in sorted(trades, key=lambda x: -x.giveback_pp)[:10]:
        print(f"  {t.symbol.replace('NSE:',''):<13} {t.horizon:<9} "
              f"peak {t.mfe_pct:+6.1f}%  final {t.actual_pct:+6.1f}%  "
              f"gave back {t.giveback_pp:5.1f}pp  ({t.exit_reason[:22]})")
    print()
    return base


def print_sweep(trades: list[Trade], base: Result, slippage_pp: float,
                mode: str) -> None:
    header = "arm\\giveback |" + "".join(f"{g:>8.0f}%" for g in GIVEBACK_GRID)

    def grid(title: str, cell) -> None:
        print("=" * len(header))
        print(title)
        print("=" * len(header))
        print(header)
        print("-" * len(header))
        for arm in ARM_GRID:
            cells = [cell(evaluate(trades, arm, gb, slippage_pp, mode))
                     for gb in GIVEBACK_GRID]
            print(f"{arm:>11.0f}% |" + "".join(cells))
        print()

    print()
    print("#" * len(header))
    print(f"#  {mode.upper()} BOUND  —  slippage {slippage_pp:.1f}pp")
    print("#" * len(header))
    grid(f"mean P&L% per trade (baseline {base.mean_pct:+.2f}%)",
         lambda r: f"{r.mean_pct:>+9.2f}")
    grid(f"profit factor (baseline {base.profit_factor:.2f})",
         lambda r: f"{r.profit_factor:>9.2f}" if r.profit_factor != float("inf")
         else f"{'inf':>9}")
    grid("winners hurt — count / pp surrendered  (the bar: ZERO)",
         lambda r: f"{r.winners_hurt:>3}/{abs(r.winners_hurt_pp):>5.1f}")


def print_detail(trades: list[Trade], arm: float, gb: float, slippage_pp: float,
                 base: Result, mode: str) -> None:
    r = evaluate(trades, arm, gb, slippage_pp, mode)
    print("=" * 78)
    print(f"DETAIL [{mode}] — arm {arm:.0f}%, give-back {gb:.0f}% of peak, "
          f"slippage {slippage_pp:.1f}pp")
    print("=" * 78)
    print(f"{'metric':<22}{'baseline':>12}{'with rule':>12}{'delta':>12}")
    print("-" * 58)
    rows = [
        ("mean P&L %", base.mean_pct, r.mean_pct),
        ("median P&L %", base.median_pct, r.median_pct),
        ("win rate %", base.win_rate, r.win_rate),
        ("avg win %", base.avg_win, r.avg_win),
        ("avg loss %", base.avg_loss, r.avg_loss),
        ("worst trade %", base.worst, r.worst),
    ]
    for name, b, n in rows:
        print(f"{name:<22}{b:>12.2f}{n:>12.2f}{n - b:>+12.2f}")
    pf_b = base.profit_factor if base.profit_factor != float("inf") else 0
    pf_n = r.profit_factor if r.profit_factor != float("inf") else 0
    print(f"{'profit factor':<22}{pf_b:>12.2f}{pf_n:>12.2f}{pf_n - pf_b:>+12.2f}")
    print("-" * 58)
    print(f"rule fired on   : {r.fired}/{r.n} trades")
    print(f"winners hurt    : {r.winners_hurt}  ({r.winners_hurt_pp:+.1f}pp surrendered)")
    print(f"losers improved : {r.losers_saved}  ({r.losers_saved_pp:+.1f}pp saved)")
    print()
    print("Trades the rule would have changed:")
    print(f"  {'symbol':<13}{'horizon':<10}{'peak%':>8}{'actual%':>9}{'ruled%':>9}{'delta':>9}")
    changed = []
    for t in trades:
        new_pct, fired = apply_rule(t, arm, gb, slippage_pp, mode)
        if fired:
            changed.append((t, new_pct, new_pct - t.actual_pct))
    for t, new_pct, delta in sorted(changed, key=lambda x: x[2]):
        print(f"  {t.symbol.replace('NSE:',''):<13}{t.horizon:<10}{t.mfe_pct:>8.1f}"
              f"{t.actual_pct:>9.2f}{new_pct:>9.2f}{delta:>+9.2f}")
    if not changed:
        print("  (none)")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["api", "db", "file"], default="api")
    ap.add_argument("--api", default=os.getenv("DASHBOARD_URL", DEFAULT_API))
    ap.add_argument("--db", default="dashboard.db")
    ap.add_argument("--file", default="journal.json")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--horizon", default=None, help="SWING or LONGTERM (default: both)")
    ap.add_argument("--slippage-pp", type=float, default=0.5,
                    help="percentage points given up to gap/slippage on a trail exit")
    ap.add_argument("--mode", choices=["optimistic", "pessimistic", "both"], default="both",
                    help="which side of the ordering bracket to compute (see module docstring)")
    ap.add_argument("--arm", type=float, default=None, help="detail run: arm %%")
    ap.add_argument("--giveback", type=float, default=None, help="detail run: give-back %% of peak")
    ap.add_argument("--out", default=None, help="write results as JSON to this path")
    args = ap.parse_args()

    trades, skips = load_trades(args)
    if not trades:
        print("No usable trades — nothing to evaluate.", file=sys.stderr)
        return 1

    modes = ["optimistic", "pessimistic"] if args.mode == "both" else [args.mode]

    base = print_baseline(trades, skips)
    for mode in modes:
        print_sweep(trades, base, args.slippage_pp, mode)

    if args.arm is not None and args.giveback is not None:
        for mode in modes:
            print_detail(trades, args.arm, args.giveback, args.slippage_pp, base, mode)

    if args.out:
        payload = {
            "baseline": {k: getattr(base, k) for k in Result.__slots__},
            "slippage_pp": args.slippage_pp,
            "horizon": args.horizon,
            "grid": [
                {"arm": a, "giveback": g, "mode": m,
                 **{k: getattr(evaluate(trades, a, g, args.slippage_pp, m), k)
                    for k in Result.__slots__}}
                for m in modes for a in ARM_GRID for g in GIVEBACK_GRID
            ],
        }
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"wrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
