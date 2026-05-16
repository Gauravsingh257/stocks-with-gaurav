"""PHASE F-FORENSICS — trade-by-trade autopsy from REAL backtest output.
Read-only. Consumes raw_*.json (committed), emits trade_database.csv + cuts.
No synthetic data: every row is an actual simulated trade from the engine."""
import json, csv, glob, os, statistics as st
from collections import defaultdict

REGIME = {
    "raw_bull_validation.json":   ("SWING", "bull_validation 2026-01..04"),
    "raw_choppy_recovery.json":   ("SWING", "choppy_recovery 2025-02..05"),
    "raw_bear_down.json":         ("SWING", "bear_down 2024-06..09"),
    "raw_sideways_down.json":     ("SWING", "sideways_down 2024-10..2025-01"),
    "raw_deteriorating.json":     ("SWING", "deteriorating 2025-09..12"),
    "raw_lt_early2025.json":      ("LONGTERM", "lt_early2025 2025-01..03"),
    "raw_lt_mid2025.json":        ("LONGTERM", "lt_mid2025 2025-06..08"),
}
HERE = os.path.dirname(__file__)


def setup_bucket(s: str) -> str:
    s = (s or "").upper()
    if "CHOCH" in s: return "CHOCH_reversal"
    if "BOS" in s and ("STRONG_BULL" in s or "BULLISH" in s): return "BOS_continuation"
    if "BOS" in s: return "BOS_other"
    if "ATR" in s: return "ATR_fallback"
    return "other"


rows = []
for path in sorted(glob.glob(os.path.join(HERE, "raw_*.json"))):
    fn = os.path.basename(path)
    if fn not in REGIME:
        continue
    horizon, regime = REGIME[fn]
    d = json.load(open(path))
    for t in d.get("trades", []):
        entry = t.get("entry") or 0
        sl = t.get("stop_loss") or 0
        tgt = t.get("target") or 0
        risk = abs(entry - sl) / entry * 100 if entry else 0
        rew = abs(tgt - entry) / entry * 100 if entry else 0
        rows.append({
            "regime": regime, "horizon": horizon,
            "symbol": t.get("symbol"), "sector": t.get("sector") or "Unknown",
            "setup": t.get("setup"), "setup_bucket": setup_bucket(t.get("setup")),
            "scan_date": t.get("scan_date"), "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"), "entry": entry, "stop_loss": sl,
            "target": tgt, "exit_price": t.get("exit_price"),
            "exit_reason": t.get("exit_reason"), "return_pct": t.get("return_pct"),
            "gross_return_pct": t.get("gross_return_pct"),
            "confidence": t.get("confidence"),
            "risk_dist_pct": round(risk, 2), "reward_dist_pct": round(rew, 2),
            "planned_rr": round(rew / risk, 2) if risk else 0,
        })

# ── trade DB CSV ──────────────────────────────────────────────────────────
cols = list(rows[0].keys())
with open(os.path.join(HERE, "trade_database.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)

def agg(group_key):
    g = defaultdict(list)
    for r in rows:
        g[r[group_key]].append(r)
    out = []
    for k, v in g.items():
        rets = [x["return_pct"] for x in v]
        wins = [x for x in rets if x > 0]
        out.append((k, len(v), round(len(wins)/len(v)*100,1),
                    round(sum(rets)/len(v),2),
                    round(min(rets),1), round(max(rets),1)))
    return sorted(out, key=lambda x: -x[3])

def line(title, data, hdr):
    print(f"\n### {title}")
    print(hdr)
    for d in data:
        print("  " + " | ".join(str(x) for x in d))

print(f"TOTAL TRADES: {len(rows)}  ({sum(1 for r in rows if r['horizon']=='SWING')} SWING / "
      f"{sum(1 for r in rows if r['horizon']=='LONGTERM')} LONGTERM)")

ex = defaultdict(int)
for r in rows: ex[r["exit_reason"]] += 1
print("\n### EXIT REASON MIX:", dict(ex))

line("BY REGIME (n | win% | exp% | worst | best)", agg("regime"),
     "  regime | n | win% | exp% | worst | best")
line("BY SETUP BUCKET", agg("setup_bucket"),
     "  bucket | n | win% | exp% | worst | best")
line("BY SECTOR (top/bottom)", agg("sector")[:6] + [("...",)] + agg("sector")[-6:],
     "  sector | n | win% | exp% | worst | best")
line("BY EXIT REASON", agg("exit_reason"),
     "  exit | n | win% | exp% | worst | best")
line("BY HORIZON", agg("horizon"),
     "  horizon | n | win% | exp% | worst | best")

# win/loss asymmetry
sw = [r for r in rows if r["horizon"]=="SWING"]
w = [r["return_pct"] for r in sw if r["return_pct"]>0]
l = [r["return_pct"] for r in sw if r["return_pct"]<=0]
print(f"\n### SWING ASYMMETRY: n={len(sw)} win%={len(w)/len(sw)*100:.1f} "
      f"avgWin={sum(w)/len(w):.2f}% avgLoss={sum(l)/len(l):.2f}% "
      f"payoff={abs((sum(w)/len(w))/(sum(l)/len(l))):.2f} exp={sum(r['return_pct'] for r in sw)/len(sw):.2f}%")

# planned vs realized RR & time-exit drift
te = [r for r in sw if r["exit_reason"]=="TIME_EXIT"]
th = [r for r in sw if r["exit_reason"]=="TARGET_HIT"]
sh = [r for r in sw if r["exit_reason"]=="STOP_LOSS"]
print(f"\n### ENTRY/EXIT TIMING (SWING)")
print(f"  planned_RR mean={st.mean(r['planned_rr'] for r in sw):.2f} (engine targets ~3R)")
print(f"  TARGET_HIT: n={len(th)} avgRet={st.mean(r['return_pct'] for r in th):.2f}% — only {len(th)/len(sw)*100:.0f}% of trades reach target")
print(f"  TIME_EXIT : n={len(te)} avgRet={st.mean(r['return_pct'] for r in te):.2f}% — the dominant outcome; setups stall")
print(f"  STOP_LOSS : n={len(sh)} avgRet={st.mean(r['return_pct'] for r in sh):.2f}%")

# biggest winners / losers
srt = sorted(rows, key=lambda r: r["return_pct"])
print("\n### 10 BIGGEST LOSERS")
for r in srt[:10]:
    print(f"  {r['symbol']:18s} {r['regime'][:22]:22s} {r['setup_bucket']:16s} {r['sector'][:12]:12s} "
          f"entry={r['entry']:.1f} exit={r['exit_price']:.1f} {r['exit_reason']:10s} {r['return_pct']:+.1f}%")
print("\n### 10 BIGGEST WINNERS")
for r in srt[-10:][::-1]:
    print(f"  {r['symbol']:18s} {r['regime'][:22]:22s} {r['setup_bucket']:16s} {r['sector'][:12]:12s} "
          f"entry={r['entry']:.1f} exit={r['exit_price']:.1f} {r['exit_reason']:10s} {r['return_pct']:+.1f}%")
