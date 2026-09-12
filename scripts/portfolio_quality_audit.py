#!/usr/bin/env python
"""
scripts/portfolio_quality_audit.py — OLD vs NEW engine portfolio-quality audit.

READ-ONLY. Touches no position, no flag, no trading logic. Every number here is
derived from the canonical `trade_lifecycle` ledger, which reconciles 1:1 with
portfolio_journal / momentum_journal (see /api/lifecycle/validate).

WHY THIS EXISTS
The 2026-09-13 ad-hoc audit produced a headline that looked catastrophic — NEW
cohort 13.3% win vs OLD 53.8% — and was **wrong**, because only 48% of the NEW
cohort had resolved and median holding period had fallen 40 -> 2 days purely as
a function of a shrinking exposure window. Re-deriving that comparison by hand
each time invites the same error. This encodes the definitions and the guards
once, so the comparison is reproducible and refuses to lie.

THE THREE RULES IT ENFORCES
  1. Cohorts are split on **position creation**, never on close date. A change
     can only be judged on the trades it *selected*.
  2. A cohort is **not reportable** until it is substantially resolved. Closed
     trades are a censored sample: winners stay open, stop-outs close fast, so
     an immature cohort is disproportionately losers. Outcome metrics are
     suppressed below MIN_RESOLVED_PCT / MIN_N and the report says so.
  3. **Entry-time metrics** (stop width, RR-at-entry, entry gap, entry type,
     sector, regime) are reported for every cohort regardless of maturity —
     they are set when the position opens, so outcome cannot bias them. These
     are the honest early signal; outcome metrics are the late one.

Usage:
    python -m scripts.portfolio_quality_audit                 # all cohorts
    python -m scripts.portfolio_quality_audit --book SWING
    python -m scripts.portfolio_quality_audit --json out.json
    python -m scripts.portfolio_quality_audit --api https://...   # remote
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")

# ── Cohort boundaries ────────────────────────────────────────────────────────
# Each boundary is the date a *selection- or risk-affecting* change reached
# production, so a cohort is "the trades chosen under this configuration".
# Dates are position-creation dates in IST. Ranges are [start, next_start).
#
# Deliberately NOT boundaries: the phantom-alert fix (2026-09-02, config-only,
# changed no selection) and the shadow/measurement fixes of 2026-09-13, which
# changed no trading behaviour at all.
COHORTS = [
    ("pre_risk",  None,         "2026-07-09", "before the risk engine — no stop cap, no risk-normalised sizing"),
    ("risk",      "2026-07-09", "2026-08-23", "risk engine live (PR #90): stop cap, sizing, liquidity down-size, trend-break"),
    ("selection", "2026-08-23", "2026-09-13", "Phase 0/1/2 selection + universe (PRs #170-180); stale-exit restored 08-31"),
    ("anchor10",  "2026-09-13", None,         "ENTRY_ANCHOR_MAX_GAP_PCT=10 — entries anchored within 10% of CMP"),
]

# Maturity gates. Below either, OUTCOME metrics are withheld as uninterpretable.
MIN_RESOLVED_PCT = 80.0
MIN_N = 30


def _get(url: str):
    try:
        import requests
        r = requests.get(url, timeout=90, headers={"User-Agent": "pq-audit/1"})
        r.raise_for_status()
        return r.json()
    except ImportError:
        pass
    except Exception as exc:
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None
    try:
        with urllib.request.urlopen(url, timeout=90) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None


def load_ledger(api: str, limit: int = 2000) -> list[dict]:
    """Every lifecycle row that reached POSITION stage. Research-only ideas are
    excluded — an idea that never filled is not a portfolio outcome."""
    out, offset = [], 0
    while True:
        d = _get(f"{api}/api/lifecycle/trades?limit=500&offset={offset}")
        if not d:
            break
        items = d.get("items") or []
        out.extend(items)
        if not d.get("has_more") or len(out) >= limit:
            break
        offset += 500
    return [r for r in out if r.get("portfolio") and not int(r.get("is_duplicate") or 0)]


def cohort_of(created: str) -> str | None:
    day = (created or "")[:10]
    if not day:
        return None
    for name, start, end, _ in COHORTS:
        if (start is None or day >= start) and (end is None or day < end):
            return name
    return None


RESOLVED = {"STOP_HIT", "TARGET_HIT", "TIME_EXIT", "FORCED_EXIT", "MANUAL_CLOSED",
            "STALE_EXIT", "TREND_BREAK", "STRUCTURE_BREAK", "EXPIRED", "NEVER_EXECUTED"}


def is_resolved(r: dict) -> bool:
    return r.get("exit_at") is not None or str(r.get("status") or "").upper() in RESOLVED


def ctx(r: dict) -> dict:
    c = r.get("context_json")
    if isinstance(c, str):
        try:
            return json.loads(c)
        except Exception:
            return {}
    return c or {}


def _f(v):
    try:
        f = float(v)
        return f if f == f else None       # NaN-safe
    except (TypeError, ValueError):
        return None


def entry_metrics(r: dict) -> dict:
    """Metrics fixed at entry — never biased by outcome, so always reportable."""
    e, s, t = _f(r.get("entry_price")), _f(r.get("stop_loss")), _f(r.get("target_1"))
    stop_w = ((e - s) / e * 100) if (e and s and 0 < s < e) else None
    rr = ((t - e) / (e - s)) if (e and s and t and e > s) else None
    c = ctx(r)
    return {
        "stop_width_pct": stop_w,
        "rr_at_entry": rr,
        "entry_gap_pct": _f(c.get("entry_gap_pct")),
        "sector": c.get("sector"),
        "regime": c.get("regime_at_entry") or c.get("regime"),
        "confidence": _f(r.get("confidence")),
    }


def summarize(rows: list[dict]) -> dict:
    res = [r for r in rows if is_resolved(r)]
    openr = [r for r in rows if not is_resolved(r)]
    n, nres = len(rows), len(res)
    resolved_pct = (100.0 * nres / n) if n else 0.0
    em = [entry_metrics(r) for r in rows]

    def q(vals, p):
        v = sorted(x for x in vals if x is not None)
        return v[min(int(p * len(v)), len(v) - 1)] if v else None

    def stat(key):
        v = [m[key] for m in em if m[key] is not None]
        if not v:
            return None
        return {"n": len(v), "median": round(st.median(v), 2),
                "p90": round(q(v, .9), 2), "max": round(max(v), 2)}

    out = {
        "n_total": n, "n_resolved": nres, "n_open": len(openr),
        "resolved_pct": round(resolved_pct, 1),
        "mature": bool(nres >= MIN_N and resolved_pct >= MIN_RESOLVED_PCT),
        # ── entry-time quality: ALWAYS reported ──
        "entry_quality": {
            "stop_width_pct": stat("stop_width_pct"),
            "rr_at_entry": stat("rr_at_entry"),
            "entry_gap_pct": stat("entry_gap_pct"),
            "confidence": stat("confidence"),
            "stop_width_over_10pct": sum(
                1 for m in em if (m["stop_width_pct"] or 0) > 10),
            "sector_mix": dict(Counter(m["sector"] for m in em if m["sector"]).most_common(6)),
            "regime_mix": dict(Counter(m["regime"] for m in em if m["regime"]).most_common(4)),
        },
    }

    if not out["mature"]:
        out["outcomes"] = None
        out["why_withheld"] = (
            f"cohort is {resolved_pct:.0f}% resolved with n={nres} closed "
            f"(need >={MIN_RESOLVED_PCT:.0f}% and n>={MIN_N}). Closed trades are a "
            "censored sample — winners stay open, stop-outs close fast — so outcome "
            "metrics here would overstate losses. Entry-time metrics above are valid.")
        # One honest signal from the open book, clearly labelled as unrealised.
        up = [_f(r.get("pnl_pct")) for r in openr]
        up = [x for x in up if x is not None]
        if up:
            out["open_unrealised"] = {"n": len(up), "mean_pct": round(st.mean(up), 2),
                                      "positive": sum(1 for x in up if x > 0)}
        return out

    pl = [_f(r.get("pnl_pct")) for r in res]
    pl = [x for x in pl if x is not None]
    wins = [x for x in pl if x > 0]
    loss = [x for x in pl if x <= 0]
    gains, losses = sum(wins), abs(sum(loss))
    days = [r.get("holding_days") for r in res if r.get("holding_days") is not None]
    out["outcomes"] = {
        "win_rate_pct": round(100 * len(wins) / len(pl), 1) if pl else None,
        "mean_pct": round(st.mean(pl), 2) if pl else None,
        "median_pct": round(st.median(pl), 2) if pl else None,
        "avg_win_pct": round(st.mean(wins), 2) if wins else None,
        "avg_loss_pct": round(st.mean(loss), 2) if loss else None,
        "profit_factor": round(gains / losses, 2) if losses else None,
        "expectancy_pct": round(st.mean(pl), 2) if pl else None,
        "median_holding_days": st.median(days) if days else None,
        "exit_mix": dict(Counter(str(r.get("exit_reason") or r.get("status") or "?").split(":")[0]
                                 for r in res).most_common(6)),
    }
    return out


def holding_matched(a: list[dict], b: list[dict], max_days: int = 7) -> dict:
    """Compare only trades that resolved inside the same window.

    This is the single most important control: it removes the exposure-window
    artefact that made the 2026-09-13 comparison unreadable. A young cohort can
    only contain fast resolutions, so matching the window is the only way to ask
    'given the same time on risk, did outcomes differ?'."""
    def g(rows):
        v = [_f(r.get("pnl_pct")) for r in rows
             if is_resolved(r) and (r.get("holding_days") or 0) <= max_days]
        return [x for x in v if x is not None]
    ga, gb = g(a), g(b)
    if len(ga) < 10 or len(gb) < 10:
        return {"comparable": False,
                "note": f"n={len(ga)} vs {len(gb)}; need >=10 each for a matched read"}
    return {
        "comparable": True, "max_days": max_days,
        "old": {"n": len(ga), "mean_pct": round(st.mean(ga), 2),
                "win_rate_pct": round(100 * sum(1 for x in ga if x > 0) / len(ga), 1)},
        "new": {"n": len(gb), "mean_pct": round(st.mean(gb), 2),
                "win_rate_pct": round(100 * sum(1 for x in gb if x > 0) / len(gb), 1)},
        "gap_pp": round(st.mean(ga) - st.mean(gb), 2),
        "permutation_p": _perm_p(ga, gb),
    }


def _perm_p(a: list[float], b: list[float], n_iter: int = 20000) -> float:
    import random
    random.seed(17)
    obs = st.mean(a) - st.mean(b)
    pool, na, hits = a + b, len(a), 0
    for _ in range(n_iter):
        random.shuffle(pool)
        if (st.mean(pool[:na]) - st.mean(pool[na:])) >= obs:
            hits += 1
    return round(hits / n_iter, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--book", default="ALL", choices=["ALL", "SWING", "LONGTERM", "MOMENTUM"])
    ap.add_argument("--json", default=None)
    ap.add_argument("--match-days", type=int, default=7)
    args = ap.parse_args()

    rows = load_ledger(args.api)
    if args.book != "ALL":
        rows = [r for r in rows if str(r.get("portfolio") or "").upper() == args.book]
    if not rows:
        print("no ledger rows — is the API reachable?")
        return 1

    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        c = cohort_of(r.get("created_at") or r.get("idea_at") or "")
        if c:
            buckets[c].append(r)

    report = {"book": args.book, "n_rows": len(rows),
              "min_resolved_pct": MIN_RESOLVED_PCT, "min_n": MIN_N, "cohorts": {}}

    W = 78
    print("=" * W)
    print(f"SWG PORTFOLIO QUALITY AUDIT — book={args.book}  ledger rows={len(rows)}")
    print("Entry-time metrics are always valid. Outcome metrics appear only when a")
    print(f"cohort is >={MIN_RESOLVED_PCT:.0f}% resolved with n>={MIN_N}.")
    print("=" * W)

    for name, start, end, why in COHORTS:
        g = buckets.get(name, [])
        s = summarize(g) if g else None
        report["cohorts"][name] = {"window": [start, end], "rationale": why, "summary": s}
        print(f"\n### {name.upper()}  [{start or 'start'} .. {end or 'now'}]  n={len(g)}")
        print(f"    {why}")
        if not g:
            print("    (no positions)")
            continue
        print(f"    resolved {s['n_resolved']}/{s['n_total']} ({s['resolved_pct']}%)"
              f"   mature={s['mature']}")
        eq = s["entry_quality"]
        for k in ("stop_width_pct", "rr_at_entry", "entry_gap_pct", "confidence"):
            v = eq[k]
            if v:
                print(f"      {k:16s} n={v['n']:>3}  median {v['median']:>7}  "
                      f"p90 {v['p90']:>7}  max {v['max']:>7}")
        print(f"      {'stop_w >10%':16s} {eq['stop_width_over_10pct']}")
        if eq["sector_mix"]:
            print(f"      sector_mix     {eq['sector_mix']}")
        if eq["regime_mix"]:
            print(f"      regime_mix     {eq['regime_mix']}")
        if s["outcomes"]:
            o = s["outcomes"]
            print(f"      OUTCOMES  win {o['win_rate_pct']}%  mean {o['mean_pct']}%  "
                  f"PF {o['profit_factor']}  medDays {o['median_holding_days']}")
            print(f"                exits {o['exit_mix']}")
        else:
            print(f"      OUTCOMES  WITHHELD — {s['why_withheld']}")
            if s.get("open_unrealised"):
                u = s["open_unrealised"]
                print(f"                open book (unrealised, NOT a result): n={u['n']} "
                      f"mean {u['mean_pct']}%  positive {u['positive']}")

    # Headline matched comparison, oldest complete vs newest.
    old = buckets.get("risk", []) + buckets.get("pre_risk", [])
    new = buckets.get("selection", []) + buckets.get("anchor10", [])
    m = holding_matched(old, new, args.match_days)
    report["holding_matched"] = m
    print("\n" + "=" * W)
    print(f"HOLDING-PERIOD-MATCHED COMPARISON (<= {args.match_days}d) — removes the exposure artefact")
    if m.get("comparable"):
        print(f"  OLD n={m['old']['n']:>3} mean {m['old']['mean_pct']:+.2f}% win {m['old']['win_rate_pct']}%")
        print(f"  NEW n={m['new']['n']:>3} mean {m['new']['mean_pct']:+.2f}% win {m['new']['win_rate_pct']}%")
        print(f"  gap {m['gap_pp']:+.2f}pp   permutation p={m['permutation_p']}  "
              f"-> {'SIGNIFICANT' if m['permutation_p'] < 0.05 else 'NOT significant'}")
    else:
        print(f"  not comparable — {m['note']}")
    print("=" * W)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
