"""
scripts/audit_open_positions_vs_policy.py
=========================================
STEP 1 of the 2026-08 portfolio remediation roadmap — READ-ONLY.

Answers one question for every currently-held SWING and LONGTERM position:

    "If this stock were trying to enter the portfolio TODAY, would policy
     accept or reject it — and exactly which gate would reject it?"

WHY THE OUTPUT IS SPLIT IN TWO
------------------------------
There are two different policies in this codebase, and conflating them would
poison the evidence base Step 4 depends on:

  ENFORCED TODAY   gates that actually run in production right now. A position
                   failing here is a genuine legacy holding — admitted under
                   rules that no longer exist.
                     * stop-width cap        risk_engine MAX_STOP_PCT (10% swing)
                                             / MAX_STOP_LONGTERM_PCT (15% LT)
                                             -> REJECTS the promotion outright
                     * turnover floor        discovery/validation min_turnover_cr
                                             (Rs 1 Cr/day) -> REJECTS at Layer 1
                     * sector concentration  portfolio_risk MAX_SECTOR_EXPOSURE=3
                                             -> REJECTS, but ONLY for mapped
                                                sectors; "OTHER" bypasses it

  PROPOSED         floors that exist in code but are NOT reachable in the live
                   path (universe_quality sits behind ALPHA_V2=0), plus the
                   Step 3 admission-gate candidates. Reported as EVIDENCE for
                   choosing thresholds — never as a verdict.
                     * price floor           Rs 50   (UQ_MIN_PRICE)
                     * turnover floor        Rs 2 Cr (LIQ_MIN_TURNOVER_CR — today
                                             this only DOWN-SIZES, never rejects)
                     * turnover floor        Rs 5 Cr (MOM_MIN_TURNOVER_CR — what
                                             the momentum book already demands)
                     * ATR ceiling           4%  (ATR_SIZE_REF_PCT, today down-sizes)
                     * ATR ceiling           8%  (hard-ceiling candidate)
                     * 200-DMA availability  (<200 bars = no trend-break exit path)

Every threshold is read from the same env vars production reads, so this audit
tracks policy automatically instead of hardcoding a snapshot of it.

Mutates nothing: no promotion, no close, no DB write, no Redis write.

USAGE
    python scripts/audit_open_positions_vs_policy.py
    python scripts/audit_open_positions_vs_policy.py --horizon LONGTERM
    python scripts/audit_open_positions_vs_policy.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_API = "https://web-production-2781a.up.railway.app"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def policy() -> dict:
    """Thresholds, read from the SAME env vars production reads."""
    return {
        # --- enforced today ---
        "MAX_STOP_PCT": _f("MAX_STOP_PCT", 10.0),
        "MAX_STOP_LONGTERM_PCT": _f("MAX_STOP_LONGTERM_PCT", 15.0),
        "RESEARCH_MIN_TURNOVER_CR": _f("RESEARCH_MIN_TURNOVER_CR", 1.0),
        "MAX_SECTOR_EXPOSURE": _f("MAX_SECTOR_EXPOSURE", 3),
        # --- proposed / not enforced in the live path ---
        "UQ_MIN_PRICE": _f("UQ_MIN_PRICE", 50.0),
        "LIQ_MIN_TURNOVER_CR": _f("LIQ_MIN_TURNOVER_CR", 2.0),
        "MOM_MIN_TURNOVER_CR": _f("MOM_MIN_TURNOVER_CR", 5.0),
        "ATR_SIZE_REF_PCT": _f("ATR_SIZE_REF_PCT", 4.0),
        "ATR_HARD_CEILING_PCT": _f("ATR_HARD_CEILING_PCT", 8.0),
    }


# ── data ─────────────────────────────────────────────────────────────────────

def fetch_book(api: str, horizon: str) -> dict:
    import requests

    url = f"{api.rstrip('/')}/api/portfolio/{horizon.lower()}?limit=50"
    resp = requests.get(url, timeout=90)
    resp.raise_for_status()
    return resp.json()


def market_metrics(symbols: list[str]) -> dict[str, dict]:
    """Live price / 20d turnover (Rs Cr per day) / 14d ATR% per symbol."""
    import warnings

    warnings.filterwarnings("ignore")
    import numpy as np
    import yfinance as yf

    out: dict[str, dict] = {}
    for sym in symbols:
        base = sym.replace("NSE:", "").strip().upper()
        rec: dict = {"price": None, "turnover_cr": None, "atr_pct": None,
                     "bars": 0, "note": ""}
        try:
            df = yf.Ticker(base + ".NS").history(period="14mo").dropna()
            if df.empty:
                rec["note"] = "no_data"
            else:
                c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
                last = float(c.iloc[-1])
                rec["price"] = last
                rec["bars"] = len(df)
                rec["turnover_cr"] = float((c.tail(20) * v.tail(20)).mean()) / 1e7
                tr = np.maximum(h - l, np.maximum((h - c.shift()).abs(),
                                                  (l - c.shift()).abs()))
                if last > 0:
                    rec["atr_pct"] = float(tr.tail(14).mean()) / last * 100.0
        except Exception as exc:
            rec["note"] = f"err:{exc}"[:40]
        out[sym] = rec
    return out


# ── evaluation ───────────────────────────────────────────────────────────────

def evaluate(pos: dict, horizon: str, m: dict, p: dict,
             sector_counts: dict[str, int]) -> dict:
    from services.portfolio_risk import get_sector

    entry = float(pos["entry_price"])
    sl = float(pos.get("stop_loss") or 0)
    stop_pct = ((entry - sl) / entry * 100.0) if entry > 0 and sl > 0 else None
    sector = get_sector(pos["symbol"].replace("NSE:", ""))

    enforced: list[str] = []
    proposed: list[str] = []

    stop_cap = p["MAX_STOP_LONGTERM_PCT"] if horizon == "LONGTERM" else p["MAX_STOP_PCT"]
    if stop_pct is not None and stop_pct > stop_cap:
        enforced.append(f"stop_too_wide({stop_pct:.1f}%>{stop_cap:.0f}%)")

    turn = m.get("turnover_cr")
    if turn is not None and turn < p["RESEARCH_MIN_TURNOVER_CR"]:
        enforced.append(
            f"turnover_below_floor({turn:.2f}Cr<{p['RESEARCH_MIN_TURNOVER_CR']:.1f}Cr)")

    # Sector concentration as it would apply to a NEW entrant: the slot this
    # position already occupies is excluded from the count.
    if sector != "OTHER":
        others = sector_counts.get(sector, 0) - 1
        if others >= p["MAX_SECTOR_EXPOSURE"]:
            enforced.append(
                f"sector_limit({sector}:{others}>={p['MAX_SECTOR_EXPOSURE']:.0f})")

    price = m.get("price")
    if price is not None and price < p["UQ_MIN_PRICE"]:
        proposed.append(f"price_below_floor({price:.2f}<{p['UQ_MIN_PRICE']:.0f})")
    if turn is not None and turn < p["LIQ_MIN_TURNOVER_CR"]:
        proposed.append(f"turnover_below_2Cr({turn:.2f})")
    if turn is not None and turn < p["MOM_MIN_TURNOVER_CR"]:
        proposed.append(f"turnover_below_5Cr({turn:.2f})")

    atr = m.get("atr_pct")
    if atr is not None and atr > p["ATR_HARD_CEILING_PCT"]:
        proposed.append(f"atr_too_high({atr:.1f}%>{p['ATR_HARD_CEILING_PCT']:.0f}%)")
    elif atr is not None and atr > p["ATR_SIZE_REF_PCT"]:
        proposed.append(f"atr_above_size_ref({atr:.1f}%>{p['ATR_SIZE_REF_PCT']:.0f}%)")

    if m.get("bars", 0) and m["bars"] < 200:
        proposed.append(f"no_200dma({m['bars']}bars)")

    return {
        "symbol": pos["symbol"].replace("NSE:", ""),
        "horizon": horizon,
        "status": pos.get("status"),
        "price": price,
        "turnover_cr": turn,
        "atr_pct": atr,
        "stop_pct": stop_pct,
        "sector": sector,
        "pnl_pct": float(pos.get("profit_loss_pct") or 0),
        "days_held": int(pos.get("days_held") or 0),
        "created_at": str(pos.get("created_at") or "")[:10],
        "verdict": "REJECT" if enforced else "PASS",
        "enforced_fails": enforced,
        "proposed_fails": proposed,
        "note": m.get("note", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.getenv("DASHBOARD_URL", DEFAULT_API))
    ap.add_argument("--horizon", default=None, help="SWING or LONGTERM")
    ap.add_argument("--json", default=None)
    ap.add_argument("--include-pending", action="store_true",
                    help="also audit armed PENDING rows (they hold a slot)")
    args = ap.parse_args()

    p = policy()
    horizons = [args.horizon.upper()] if args.horizon else ["LONGTERM", "SWING"]

    from services.portfolio_risk import get_sector

    books: dict[str, list[dict]] = {}
    for hz in horizons:
        book = fetch_book(args.api, hz)
        rows = [r for r in book["items"]
                if r.get("status") == "ACTIVE"
                or (args.include_pending and r.get("status") == "PENDING")]
        books[hz] = rows
        print(f"{hz}: {len(rows)} rows audited  (book uses {book['used']}/{book['max']} slots)")

    all_syms = sorted({r["symbol"] for rows in books.values() for r in rows})
    print(f"\nFetching market metrics for {len(all_syms)} symbols...")
    metrics = market_metrics(all_syms)

    results: list[dict] = []
    for hz, rows in books.items():
        counts: dict[str, int] = {}
        for r in rows:
            s = get_sector(r["symbol"].replace("NSE:", ""))
            counts[s] = counts.get(s, 0) + 1
        for r in rows:
            results.append(evaluate(r, hz, metrics[r["symbol"]], p, counts))

    W = 122
    print()
    print("=" * W)
    print("POLICY THRESHOLDS USED")
    print("=" * W)
    print(f"  ENFORCED  stop-width cap      : swing {p['MAX_STOP_PCT']:.0f}%  |  "
          f"longterm {p['MAX_STOP_LONGTERM_PCT']:.0f}%")
    print(f"  ENFORCED  turnover floor      : Rs {p['RESEARCH_MIN_TURNOVER_CR']:.1f} Cr/day")
    print(f"  ENFORCED  sector concentration: max {p['MAX_SECTOR_EXPOSURE']:.0f} per sector "
          f"(symbols mapping to OTHER bypass this entirely)")
    print(f"  proposed  price floor         : Rs {p['UQ_MIN_PRICE']:.0f}  "
          f"(ALPHA_V2 off -> NOT live)")
    print(f"  proposed  turnover floors     : Rs {p['LIQ_MIN_TURNOVER_CR']:.0f} Cr (sizing) "
          f"| Rs {p['MOM_MIN_TURNOVER_CR']:.0f} Cr (momentum book)")
    print(f"  proposed  ATR ceilings        : {p['ATR_SIZE_REF_PCT']:.0f}% (sizing) "
          f"| {p['ATR_HARD_CEILING_PCT']:.0f}% (hard)")

    for hz in horizons:
        rows = [r for r in results if r["horizon"] == hz]
        rows.sort(key=lambda r: (r["verdict"] == "PASS", r["pnl_pct"]))
        print()
        print("=" * W)
        print(f"{hz}  —  would this be admitted TODAY?")
        print("=" * W)
        print(f"{'STOCK':<13}{'PRICE':>10}{'TURNOVER':>11}{'ATR%':>7}{'SL%':>7}  "
              f"{'SECTOR':<11}{'P/L%':>8}{'DAYS':>6}  {'POLICY':<7} WHY")
        print("-" * W)
        for r in rows:
            px = f"{r['price']:.2f}" if r["price"] is not None else "-"
            tn = f"{r['turnover_cr']:.2f}Cr" if r["turnover_cr"] is not None else "-"
            at = f"{r['atr_pct']:.1f}" if r["atr_pct"] is not None else "-"
            sp = f"{r['stop_pct']:.1f}" if r["stop_pct"] is not None else "-"
            why = ", ".join(r["enforced_fails"]) if r["enforced_fails"] else "-"
            print(f"{r['symbol']:<13}{px:>10}{tn:>11}{at:>7}{sp:>7}  "
                  f"{r['sector']:<11}{r['pnl_pct']:>+8.2f}{r['days_held']:>6}  "
                  f"{r['verdict']:<7} {why}")
        rej = [r for r in rows if r["verdict"] == "REJECT"]
        print("-" * W)
        print(f"  {len(rows) - len(rej)} PASS / {len(rej)} REJECT under policy enforced today")

    # ── reason tallies ───────────────────────────────────────────────────
    print()
    print("=" * W)
    print("REASON TALLY — the evidence base")
    print("=" * W)

    def tally(key: str, label: str) -> None:
        counts: dict[str, int] = {}
        for r in results:
            for f in r[key]:
                counts[f.split("(")[0]] = counts.get(f.split("(")[0], 0) + 1
        print(f"\n  {label}")
        if not counts:
            print("    (none)")
            return
        for code, n in sorted(counts.items(), key=lambda x: -x[1]):
            syms = [r["symbol"] for r in results
                    if any(f.startswith(code) for f in r[key])]
            print(f"    {code:<24} {n:>3}   {', '.join(syms)}")

    tally("enforced_fails", "ENFORCED TODAY — these are genuine legacy holdings")
    tally("proposed_fails", "PROPOSED (not live) — evidence for Step 3/4 thresholds")

    print()
    print("=" * W)
    print("SECTOR CONCENTRATION")
    print("=" * W)
    for hz in horizons:
        rows = [r for r in results if r["horizon"] == hz]
        if not rows:
            continue
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["sector"]] = counts.get(r["sector"], 0) + 1
        unmapped = counts.get("OTHER", 0)
        print(f"\n  {hz}:")
        for s, n in sorted(counts.items(), key=lambda x: -x[1]):
            flag = ""
            if s == "OTHER":
                flag = "   <-- UNMAPPED: exempt from the concentration limit"
            elif n > p["MAX_SECTOR_EXPOSURE"]:
                flag = f"   <-- OVER LIMIT (max {p['MAX_SECTOR_EXPOSURE']:.0f})"
            print(f"    {s:<12} {n:>2}{flag}")
        print(f"    -> {unmapped}/{len(rows)} ({unmapped / len(rows) * 100:.0f}%) of this book "
              f"is unmapped, so diversification is not enforced on it")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"policy": p, "positions": results}, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
