"""
PHASE G2-6 Step 1 — production daily-bar-clocked equity state machine
(ONLINE-correct).

WHY THIS EXISTS / WHAT STEP 1's TEST PROVED
-------------------------------------------
First attempt assumed "re-run the G2-5 batch simulator on all history up
to today, act on new fires" was equivalent to the batch backtest. The
equivalence test DISPROVED that: the batch oracle
(`state_machine_sim.simulate_state_machine_entries`) is a *batch*
algorithm — when a setup fires it skips its scan pointer forward, and a
setup unresolved at the array end is collapsed to "no fire". A live
engine seeing one bar at a time cannot skip with future knowledge, so a
naive driver fired MORE trades than the backtest measured (22 vs 20 on a
sample). The G2-5 numbers were measured on the *batch* trade set; a live
engine's trade set is genuinely different and must be re-backtested on
its OWN fires (this module is consumed by the re-run; see
ALPHA_BASELINE.md G2-6 ONLINE section).

THE ONLINE-CORRECT RULE
-----------------------
Identical setup logic to the oracle — detectors, confirmation candle,
weekly gate and the entry/SL/target formulas are *imported / replicated
verbatim* from `state_machine_sim` + `engine.*` (single source of truth
for the rules; this module only owns the SCAN CADENCE). The one
behavioural correction: a setup is acted on only when its outcome is
**final** — fired, invalidated, or genuinely expired by bar count. If
the scan reaches a FORMED setup whose resolution would need bars that do
not exist yet ("indeterminate"), the scan STOPS there — it does not
guess, and it does not advance past it (advancing past unresolved setups
is exactly what produced the phantom extra fires). That setup is simply
re-evaluated on a future day when more bars exist.

GUARANTEES (pinned by tests/test_equity_state_machine_equivalence.py)
---------------------------------------------------------------------
- Self-consistency (MUST hold exactly): day-by-day evaluation over
  growing prefixes, de-duplicated, == one evaluation on the full array.
  This is the property a live engine must have — no decision is
  provisional.
- Rule fidelity (MUST hold exactly): every fire this engine emits also
  appears, byte-identical (entry/SL/target/formed/tapped), in the batch
  oracle's output — i.e. online fires ⊆ oracle fires. It never invents a
  trade the validated rules would not; it correctly emits *fewer* (drops
  the oracle's batch-only phantom/overlap tail fires).

SCOPE / STATUS
--------------
Step 1 = this engine + the `equity_sm_state` DDL (additive, in
dashboard/backend/db/schema.py) + the equivalence test + the
online-engine re-backtest. NOTHING calls this in the live agent yet: no
agent wiring, no flag, no DB writes, no live behaviour. The persistent
`equity_sm_state` fast-path and the ARMED/TAPPED UI phase projection are
deferred to G2-6 Rung A (=shadow), behind a default-OFF flag, and must
still pass these same tests.

Pure / read-only. No network, no DB, no wall-clock.
"""

from __future__ import annotations

import logging

from engine.indicators import calculate_atr
from engine.swing import detect_daily_fvg, detect_daily_ob, detect_weekly_trend
from services.state_machine_sim import (
    StateMachineFire,
    _confirmation_candle_long,  # single-source rule primitive
    weekly_up_to,               # single-source point-in-time weekly slice
)

log = logging.getLogger("services.equity_state_machine")

_MIN_HISTORY_DEFAULT = 60
_EXPIRY_BARS_DEFAULT = 15


def scan_fires_online(
    daily: list[dict],
    weekly: list[dict],
    *,
    expiry_bars: int = _EXPIRY_BARS_DEFAULT,
    min_history: int = _MIN_HISTORY_DEFAULT,
) -> list[StateMachineFire]:
    """Online-correct single pass over `daily` (already point-in-time —
    only bars the live engine would have on the evaluation day).

    Mirrors `state_machine_sim.simulate_state_machine_entries`'s pointer
    logic exactly, with one correction: stop at the first FORMED setup
    whose outcome the available bars cannot yet determine, instead of
    treating it as no-fire and scanning past it. Every emitted fire is a
    decision future bars cannot reverse.
    """
    fires: list[StateMachineFire] = []
    n = len(daily)
    if n < min_history or len(weekly) < 12:
        return fires

    i = min_history
    while i < n:
        window = daily[: i + 1]
        wt = detect_weekly_trend(weekly_up_to(weekly, daily, i))
        if wt not in ("BULLISH", "STRONG_BULL"):
            i += 1
            continue

        ob = detect_daily_ob(window, "LONG")
        fvg = detect_daily_fvg(window, "LONG")
        if not ob or not fvg:
            i += 1
            continue

        formed_idx = i
        fvg_lo, fvg_hi = (fvg[0], fvg[1]) if fvg[0] <= fvg[1] else (fvg[1], fvg[0])
        ob_low = ob[0]
        tapped_idx = -1
        fired = False
        resolved = False  # outcome final & independent of future bars?

        # Inner loop mirrors state_machine_sim EXACTLY: after a tap it
        # keeps scanning, waiting for the FIRST confirmation candle
        # (it does not decide on the next bar). Only the loop-exit
        # classification below adds online-correctness.
        j = i + 1
        while j < n and (j - formed_idx) <= expiry_bars:
            c = daily[j]
            if c["close"] < ob_low:
                resolved = True  # invalidation — final
                break
            if tapped_idx < 0:
                if c["low"] <= fvg_hi and c["high"] >= fvg_lo:
                    tapped_idx = j
            else:
                if _confirmation_candle_long(c):
                    entry = round((fvg_lo + fvg_hi) / 2.0, 2)
                    atr = calculate_atr(daily[: j + 1], 14) or (entry * 0.02)
                    recent_low = min(x["low"] for x in daily[max(0, j - 9): j + 1])
                    sl = round(recent_low - atr * 0.3, 2)
                    if sl < entry:  # geometry sane (longs-only)
                        target = round(entry + 2.0 * (entry - sl), 2)
                        fires.append(StateMachineFire(
                            entry_idx=j, entry=entry, stop_loss=sl,
                            target=target, formed_idx=formed_idx,
                            tapped_idx=tapped_idx,
                        ))
                        fired = True
                    resolved = True  # first confirmation candle = final
                    break
            j += 1
        else:
            # inner loop ended on its while-condition (no break)
            if (j - formed_idx) > expiry_bars:
                resolved = True  # genuinely expired by bar count — final
            # else: j >= n within the expiry window → INDETERMINATE

        if not resolved:
            # First setup whose outcome future bars could still change.
            # A live engine must wait, not guess, and must NOT advance
            # past it — advancing past unresolved setups is exactly what
            # created the batch-only phantom fires.
            break

        i = (j + 1) if fired else (formed_idx + 1)

    return fires


# Public name the live daily tick / re-backtest call. A live tick passes
# `daily` = history up to that day; this returns every final fire, and the
# agent (Rung A, later) acts on the one whose entry bar is the latest.
def replay_fires(
    daily: list[dict],
    weekly: list[dict],
    *,
    expiry_bars: int = _EXPIRY_BARS_DEFAULT,
    min_history: int = _MIN_HISTORY_DEFAULT,
) -> list[StateMachineFire]:
    return scan_fires_online(
        daily, weekly, expiry_bars=expiry_bars, min_history=min_history
    )


# ─────────────────────────────────────────────────────────────────────
# PHASE G2-6 Step 2 — Rung A (=shadow). The FIRST writer of equity_sm_state
# and the first wiring into the live agent — but ONLY when the env flag
# EQUITY_STATE_MACHINE == "shadow". Unset/off ⟹ none of this is reached ⟹
# byte-identical to today. Writes ONLY lifecycle_events (source
# "g2_6_sm_shadow") + equity_sm_state. Creates NO recommendation, touches
# NO watchlist, sends NO alert. Best-effort: never raises into the agent.
# ─────────────────────────────────────────────────────────────────────

_SHADOW_SOURCE = "g2_6_sm_shadow"
_SHADOW_SETUP = "SMC_STATE_MACHINE_LONG_ONLINE"


def shadow_flag() -> str:
    """`EQUITY_STATE_MACHINE` env, lowered. "" / "off" ⟹ inert."""
    import os
    return os.getenv("EQUITY_STATE_MACHINE", "").strip().lower()


def _ist_today() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()


def _ensure_g26_tables(conn) -> None:
    """Idempotently ensure the G2-6 path's tables (lifecycle_events,
    equity_sm_state) exist, INDEPENDENT of the global init_db — which on
    production has been observed not to reach the tail of its DDL, so
    G2-3's ledger silently never persisted. Reuses the canonical DDL
    string verbatim (no schema drift); CREATE … IF NOT EXISTS so it is
    harmless when the tables already exist. Best-effort."""
    try:
        from dashboard.backend.db.schema import DDL, iter_ddl_statements

        for s in iter_ddl_statements(DDL):
            if "lifecycle_events" not in s and "equity_sm_state" not in s:
                continue
            if not s.upper().startswith(("CREATE TABLE", "CREATE INDEX")):
                continue
            try:
                conn.execute(s)
            except Exception:
                pass
        conn.commit()
    except Exception:
        pass


def _get_sm_state(conn, symbol: str, horizon: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM equity_sm_state WHERE symbol=? AND horizon=?",
        (symbol, horizon),
    ).fetchone()
    return dict(row) if row else None


def _upsert_sm_state(conn, symbol: str, horizon: str, **f) -> None:
    conn.execute(
        """
        INSERT INTO equity_sm_state
          (symbol, horizon, phase, ob_low, fvg_low, fvg_high, formed_date,
           tapped_date, bars_since_formed, planned_entry, stop_loss,
           target, last_fire_date, last_eval_date, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(symbol, horizon) DO UPDATE SET
           phase=excluded.phase, planned_entry=excluded.planned_entry,
           stop_loss=excluded.stop_loss, target=excluded.target,
           last_fire_date=excluded.last_fire_date,
           last_eval_date=excluded.last_eval_date,
           updated_at=datetime('now')
        """,
        (symbol, horizon, f.get("phase", "IDLE"), f.get("ob_low"),
         f.get("fvg_low"), f.get("fvg_high"), f.get("formed_date"),
         f.get("tapped_date"), f.get("bars_since_formed", 0),
         f.get("planned_entry"), f.get("stop_loss"), f.get("target"),
         f.get("last_fire_date"), f.get("last_eval_date")),
    )


async def run_equity_sm_shadow_tick(
    *,
    target_universe: int | None = None,
    source: str | None = None,
) -> dict:
    """Rung A shadow tick. Runs the ONLINE engine over the F2-Good+
    universe on the data available NOW (point-in-time is inherent — no
    future bars exist live), logging every new FIRE to the canonical
    ledger. Idempotent per IST day. Best-effort: ANY failure is swallowed
    (shadow must never disturb the live agent). Returns a summary dict."""
    if shadow_flag() != "shadow":
        return {"status": "disabled"}
    import os
    try:
        from dashboard.backend.db import get_connection
        from dashboard.backend.lifecycle_ledger import record_lifecycle_event
        from services.universe_manager import load_nse_universe
        from services.research_levels import daily_candles_to_weekly, df_to_candles
        from services.universe_quality import filter_symbols_by_quality
        from services.validation_engine import _fetch_frames

        uni_n = target_universe or int(os.getenv("EQUITY_SM_SHADOW_UNIVERSE", "150"))
        src = source or os.getenv("EQUITY_SM_SHADOW_SOURCE", "yfinance")
        today = _ist_today()

        universe = load_nse_universe(uni_n)
        frames = await _fetch_frames(universe.symbols, src, days=900, as_of=None)
        symbols, _q = filter_symbols_by_quality(
            universe.symbols, frames, cutoff=today, min_tier="Good"
        )

        scanned = new_fires = 0
        conn = get_connection()
        try:
            _ensure_g26_tables(conn)  # self-sufficient: don't trust global init_db
            for sym in symbols:
                daily = df_to_candles(frames.get(sym))
                if len(daily) < 70:
                    continue
                st = _get_sm_state(conn, sym, "SWING")
                if st and st.get("last_eval_date") == today:
                    continue  # idempotent: already evaluated this IST day
                scanned += 1
                last_fire = (st or {}).get("last_fire_date") or ""
                weekly = daily_candles_to_weekly(daily)
                fires = scan_fires_online(daily, weekly)

                max_fire_date = last_fire
                for fr in fires:
                    fdate = str(daily[fr.entry_idx].get("date"))[:10]
                    if fdate <= last_fire:
                        continue  # already logged on a prior tick
                    record_lifecycle_event(
                        sym, "ENTRY_ACTIVE", horizon="SWING",
                        prev_state="ARMED", source=_SHADOW_SOURCE,
                        planned_entry=fr.entry, stop_loss=fr.stop_loss,
                        target_1=fr.target, rr_planned=2.0,
                        setup=_SHADOW_SETUP,
                        details={"fire_date": fdate, "formed_idx": fr.formed_idx,
                                 "tapped_idx": fr.tapped_idx, "online": True,
                                 "shadow": True},
                    )
                    new_fires += 1
                    if fdate > max_fire_date:
                        max_fire_date = fdate

                last = fires[-1] if fires else None
                _upsert_sm_state(
                    conn, sym, "SWING",
                    phase="FIRED" if fires else "IDLE",
                    planned_entry=getattr(last, "entry", None),
                    stop_loss=getattr(last, "stop_loss", None),
                    target=getattr(last, "target", None),
                    last_fire_date=max_fire_date or None,
                    last_eval_date=today,
                )
            conn.commit()
        finally:
            conn.close()
        return {"status": "ok", "scanned": scanned, "new_fires": new_fires,
                "universe": uni_n, "ist_day": today}
    except Exception as exc:  # never propagate — shadow only
        log.warning("equity_sm shadow tick skipped: %s", exc)
        return {"status": "error", "detail": str(exc)}


def compute_shadow_scorecard(*, hold_days: int = 15, source: str = "yfinance") -> dict:
    """Read-only: score the accumulated g2_6_sm_shadow FIRE stream against
    the corrected G2-6 gate using realised forward outcomes from later
    OHLC (same exit convention as the backtest). The LIVE confirmation
    that Rung A must satisfy before Rung B. Best-effort; safe empty state
    before any shadow events exist."""
    GATE = {"expectancy": 1.0, "win_rate": 48.0, "profit_factor": 1.30,
            "acct_max_dd": 15.0}
    try:
        from dashboard.backend.db import get_connection
        from services.backtest_engine import (
            PortfolioConfig, _simulate_planned_entry_trade,
            _simulate_portfolio, _max_drawdown, _sharpe,
        )
        from services.research_levels import df_to_candles
        from services.validation_engine import _fetch_frames

        conn = get_connection()
        try:
            _ensure_g26_tables(conn)  # missing table ⟹ empty, not error
            try:
                rows = [dict(r) for r in conn.execute(
                    "SELECT symbol, planned_entry, stop_loss, target_1, "
                    "details, created_at FROM lifecycle_events WHERE "
                    "source=? AND state='ENTRY_ACTIVE' ORDER BY id ASC",
                    (_SHADOW_SOURCE,),
                ).fetchall()]
            except Exception:
                rows = []
        finally:
            conn.close()

        if not rows:
            return {"status": "initialising", "shadow_events": 0,
                    "gate": GATE, "_note": "No shadow fires logged yet — "
                    "flag EQUITY_STATE_MACHINE=shadow not active or no fires."}

        import json as _json
        syms = sorted({r["symbol"] for r in rows})
        frames = None
        try:
            import asyncio
            frames = asyncio.run(_fetch_frames(syms, source, days=900, as_of=None))
        except Exception:
            frames = {}

        scored, pending = [], 0
        fire_dates = []
        for r in rows:
            try:
                d = _json.loads(r.get("details") or "{}")
            except Exception:
                d = {}
            fdate = (d.get("fire_date") or str(r.get("created_at"))[:10])
            fire_dates.append(fdate)
            daily = df_to_candles((frames or {}).get(r["symbol"]))
            if not daily:
                pending += 1
                continue
            idx = next((k for k, c in enumerate(daily)
                        if str(c.get("date"))[:10] == fdate), None)
            if idx is None or idx + 2 >= len(daily):
                pending += 1  # no forward data yet — still maturing
                continue
            shim = type("F", (), {
                "entry_idx": idx, "entry": r["planned_entry"],
                "stop_loss": r["stop_loss"], "target": r["target_1"]})()
            tr = _simulate_planned_entry_trade(
                r["symbol"], daily, shim, hold_days, 0.10, 0.05)
            if tr is not None:
                scored.append(tr)

        if not scored:
            return {"status": "maturing", "shadow_events": len(rows),
                    "pending_forward_data": pending, "gate": GATE,
                    "fire_date_range": [min(fire_dates), max(fire_dates)]
                    if fire_dates else None}

        rets = [t.return_pct for t in scored]
        wins = [t for t in scored if t.return_pct > 0]
        gw = sum(t.return_pct for t in wins)
        gl = abs(sum(t.return_pct for t in scored if t.return_pct <= 0))
        pf = round(gw / gl, 2) if gl else 999.0
        pm = _simulate_portfolio(scored, PortfolioConfig())
        m = {
            "scored_trades": len(scored),
            "win_rate": round(len(wins) / len(scored) * 100.0, 2),
            "expectancy": round(sum(rets) / len(scored), 2),
            "profit_factor": pf,
            "naive_max_drawdown": _max_drawdown(rets),
            "sharpe": _sharpe(rets),
            "account_max_drawdown_pct": pm.get("account_max_drawdown_pct"),
            "account_total_return_pct": pm.get("account_total_return_pct"),
        }
        passed = (
            m["expectancy"] >= GATE["expectancy"]
            and m["win_rate"] >= GATE["win_rate"]
            and pf >= GATE["profit_factor"]
            and (m["account_max_drawdown_pct"] or 99) <= GATE["acct_max_dd"]
        )
        from datetime import date as _d
        span_days = 0
        if fire_dates:
            try:
                span_days = (_d.fromisoformat(max(fire_dates))
                             - _d.fromisoformat(min(fire_dates))).days
            except Exception:
                span_days = 0
        return {
            "status": "scored",
            "shadow_events": len(rows),
            "pending_forward_data": pending,
            "metrics": m, "gate": GATE,
            "gate_pass_on_scored": bool(passed),
            "portfolio_metrics": pm,
            "soak": {"fire_date_range": [min(fire_dates), max(fire_dates)],
                     "span_days": span_days,
                     "weeks": round(span_days / 7.0, 1),
                     "soak_complete": span_days >= 28 and len(scored) >= 20},
            "_note": "LIVE confirmation of the G2-6 gate on realised "
            "forward outcomes. Rung B requires soak_complete AND "
            "gate_pass_on_scored. Read-only; nothing user-facing.",
        }
    except Exception as exc:
        log.warning("shadow scorecard failed: %s", exc)
        return {"status": "error", "detail": str(exc)}
