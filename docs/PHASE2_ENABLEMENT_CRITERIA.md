# Phase 2 Flag Enablement Criteria

Decision framework for turning on the Phase-2 signal-quality flags shipped (default-OFF) in PR #40. **No flag is enabled until the criteria below are met on live shadow data.**

| Flag | Default (today) | Candidate value | Status |
|------|-----------------|-----------------|--------|
| `ENTRY_ANCHOR_MAX_GAP_PCT` | `30` (unchanged) | `10` (Anchor10) | **Shadow validation in progress** |
| `STRUCTURAL_TARGET_CAP` | `off` | `on` | **HOLD — do not enable standalone** (see §3) |

These are engine-service env vars. Setting/unsetting them is instant and code-free (the defaults reproduce current behaviour), so rollback = remove the env var.

---

## 1. `ENTRY_ANCHOR_MAX_GAP_PCT=10` — go/no-go

Record one session per trading day with:

```
python scripts/shadow_anchor_daily.py        # appends to signal_history/shadow_anchor10.csv
python scripts/shadow_anchor_daily.py --show  # view the running table
```

Collect **3–5 consecutive trading sessions**, then evaluate. **GO requires ALL of:**

| # | Criterion | Threshold | Why |
|---|-----------|-----------|-----|
| C1 | **Signal count does not collapse** | Anchor10 count ≥ 80% of current count, every session (no session with >20% drop) | Anchoring re-prices entries; it must not silently drop setups (only happens if the tightened entry inverts the stop). The recorder prints a `WARN` if a session breaches this. |
| C2 | **Actionability materially improves** | Anchor10 actionable% ≥ 60% (vs ~13% today) | The whole point: ideas land near a fillable entry. |
| C3 | **Average distance from entry tightens** | Anchor10 avg distance ≤ 6% | Confirms entries sit at the zone, not chasing. |
| C4 | **Reward stays real** | Anchor10 median remaining-RR-from-CMP ≥ 2.0 | Re-anchoring must preserve genuine reward, not manufacture actionability by gutting RR. |
| C5 | **Stability** | C1–C4 hold on **every** session in the window (no oscillation) | One good day is noise; we need consistency. |

**If all pass →** prepare the production-enablement recommendation: set `ENTRY_ANCHOR_MAX_GAP_PCT=10` on the engine service, monitor the next 2 sessions live, keep the rollback (remove the var) one click away.

**If any fail →** hold, capture the failing session(s), and investigate (e.g. a count collapse points to stop-inversion in `_scored_smc_levels` that needs a guard before enabling).

### Seed / baseline (session 1 — real 2026-06-04 scan)
| date | config | count | actionable | act% | avgDist% | medRemRR | ext>10 | quality |
|------|--------|------:|-----------:|-----:|---------:|---------:|-------:|--------:|
| 2026-06-04 | A current | 30 | 4 | 13.3 | 17.7 | 0.31 | 23 | 14.4 |
| 2026-06-04 | B anchor10 | 30 | 24 | 80.0 | 3.7 | 2.55 | 1 | 84.8 |

Session 1 passes C1 (count 30→30, 0% drop), C2 (80%), C3 (3.7%), C4 (2.55). **2–4 more sessions needed for C5.**

---

## 2. Per-recommendation impact (action #5)

To see exactly which live ideas change before enabling:
```
python scripts/shadow_diff_phase2.py --source json --path _disc.json --anchor-gap 10 --per-symbol
```
On 2026-06-04, **22/30** ideas re-anchored; every extended name flipped to actionable/waiting with remaining RR rising to ~2.5R. The 8 unchanged were already within 10% of entry.

---

## 3. `STRUCTURAL_TARGET_CAP` — HOLD

Exact validation (real candle history + the production `_nearest_resistance_above` pivot finder) was run via:
```
python scripts/shadow_diff_phase2.py --source json --path _disc.json --mode engine --anchor-gap 10
```
**Result: enabling the cap on today's (un-anchored) book makes it worse** — median remaining-RR-from-CMP fell to **−0.18** (vs −/+0 baseline), because capping targets on already-extended setups only confirms there is no upside left.

**Conclusion:** the structural cap is **not** a standalone improvement. It is only coherent **combined with** entry anchoring (entry near CMP → the capped target still leaves room). Therefore:
- Keep `STRUCTURAL_TARGET_CAP=off`.
- Re-evaluate the cap **only after** Anchor10 is live and stable, using a combined shadow run (anchor + cap), and only enable if the combined median remaining RR stays ≥ 1.5 while targets become more realistic.

---

## 4. Rollback

Both flags are env vars whose defaults preserve current behaviour:
- Disable Anchor10: remove/`=30` `ENTRY_ANCHOR_MAX_GAP_PCT` on the engine service.
- Disable cap: remove/`=0` `STRUCTURAL_TARGET_CAP`.

No code deploy required; effect is immediate on the next scan.
