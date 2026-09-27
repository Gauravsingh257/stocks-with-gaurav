"""Phase 4A — Long-Term shadow selector: deterministic, budget-matched, persisted, and isolated."""
from __future__ import annotations

import asyncio
import copy
import json
import random
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import services.lt_shadow_selector as ss
from dashboard.backend.db import lt_shadow as store
from services import fundamental_analysis as fa


def _inp(sym, fund, *, ctrl=False, growth=0.5, quality=0.5, tech=50.0, entry=100.0, l2=True, turnover=5.0):
    return ss.ShadowInput(
        symbol=sym, cmp=105.0, avg_turnover_cr=turnover, layer2_pass=l2, entry=entry, stop_loss=90.0,
        targets=[130.0], setup="SMC_LONGTERM", technical_score=tech, smc_score=60.0, control_selected=ctrl,
        fund_source="yfinance" if fund is not None else None, fundamental_score_de_fixed=fund,
        fundamental_score_legacy=fund, growth=growth, quality=quality, debt_equity_ratio=0.1,
        raw_debt_equity_legacy=0.1,
    )


def _pool(n=40, seed=7):
    rng = random.Random(seed)
    return [_inp(f"NSE:S{i:02d}", rng.random(), ctrl=i % 5 == 0, growth=rng.random(), quality=rng.random(),
                 tech=rng.random() * 100) for i in range(n)]


def _key(run):
    return [(p.symbol, p.rank, p.score, p.selected, p.control_selected, p.components) for p in run.picks]


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("LT_SHADOW_SELECTOR_ENABLED", raising=False)
    assert ss.lt_shadow_enabled() is False


def test_selection_is_deterministic_under_any_input_order():
    pool = _pool()
    base = ss.select(pool, scan_id="S", date="2026-09-28", min_turnover_cr=1.0)
    for seed in range(5):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)
        assert _key(ss.select(shuffled, scan_id="S", date="2026-09-28", min_turnover_cr=1.0)) == _key(base)


def test_budget_matches_the_control_count():
    pool = _pool()
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0, top_n=30)
    assert run.control_count == run.budget == 8
    assert len(run.selected) == 8
    top = [p for p in run.picks if p.rank is not None and p.rank <= 30]
    assert [p.rank for p in top] == list(range(1, 31))
    assert all(p.selected == (p.rank <= 8) for p in run.picks if p.rank is not None)
    extra = [p for p in run.picks if p.rank is None or p.rank > 30]
    assert extra and all(p.control_selected for p in extra)   # only control picks beyond the top-N


def test_logging_extension_does_not_change_the_selection():
    """Phase 4B only adds logged rows. The selected set must equal the plain score order."""
    pool = _pool(60, seed=3)
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0, top_n=30)
    ranked = sorted((p for p in run.picks if p.rank is not None), key=lambda p: p.rank)
    assert [p.rank for p in ranked] == sorted({p.rank for p in ranked})
    assert [p.symbol for p in run.selected] == [p.symbol for p in ranked[: run.budget]]
    assert all(ranked[k].score >= ranked[k + 1].score for k in range(len(ranked) - 1))


def test_every_control_pick_is_logged_with_rank_or_exclusion():
    pool = [_inp(f"NSE:G{i:02d}", 0.02 * i) for i in range(40)]
    pool += [_inp("NSE:CTRL_LOW", 0.001, ctrl=True), _inp("NSE:CTRL_NOFUND", None, ctrl=True),
             _inp("NSE:CTRL_ILLIQ", 0.9, ctrl=True, turnover=0.1)]
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0, top_n=5)
    by = {p.symbol: p for p in run.picks}
    assert by["NSE:CTRL_LOW"].rank == 40 and by["NSE:CTRL_LOW"].exclusion is None
    assert by["NSE:CTRL_NOFUND"].rank is None and by["NSE:CTRL_NOFUND"].exclusion == "no_real_fundamentals"
    assert by["NSE:CTRL_ILLIQ"].exclusion == "illiquid"
    assert run.budget == 3 and len(run.selected) == 3 and not any(by[s].selected for s in by if s.startswith("NSE:CTRL"))


def test_coverage_measures_fundamentals_on_the_common_support():
    pool = [_inp(f"NSE:F{i}", 0.5) for i in range(6)] + [_inp(f"NSE:N{i}", None) for i in range(4)]
    pool += [_inp("NSE:ILLIQ", 0.5, turnover=0.1), _inp("NSE:ILLIQ_NOFUND", None, turnover=0.1)]
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0)
    assert run.coverage == {"universe_real_fundamentals": 7, "support_ex_fundamentals": 10,
                            "support_with_fundamentals": 6, "support_coverage_pct": 60.0}


def test_zero_control_selects_nothing_but_still_logs_the_ranking():
    pool = [_inp(f"NSE:A{i}", 0.1 * i) for i in range(10)]
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0, top_n=5)
    assert run.budget == 0 and run.selected == [] and len(run.picks) == 5


def test_eligibility_exclusions_are_counted():
    pool = [
        _inp("NSE:OK", 0.9),
        _inp("NSE:NOFUND", None),
        _inp("NSE:ILLIQ", 0.9, turnover=0.2),
        _inp("NSE:L2", 0.9, l2=False),
        _inp("NSE:NOPLAN", 0.9, entry=None),
    ]
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0)
    assert [p.symbol for p in run.picks] == ["NSE:OK"]
    assert run.exclusions == {"no_real_fundamentals": 1, "illiquid": 1, "failed_layer2_quality": 1,
                              "no_tradable_plan": 1}


def test_percentile_averages_ties_and_keeps_none():
    out = ss._percentile({"a": 1.0, "b": 1.0, "c": 3.0, "d": None})
    assert out == {"a": 0.25, "b": 0.25, "c": 1.0, "d": None}


def test_missing_component_renormalises_instead_of_zeroing():
    pool = [_inp("NSE:A", 0.9, tech=None), _inp("NSE:B", 0.1, tech=None)]
    run = ss.select(pool, scan_id="S", date="d", min_turnover_cr=1.0)
    assert set(run.picks[0].components) == {"fundamental", "growth", "quality"}


INFY = {"trailingPE": 28.0, "priceToBook": 7.5, "returnOnEquity": 0.31, "revenueGrowth": 0.06,
        "earningsGrowth": 0.08, "debtToEquity": 9.541, "heldPercentInsiders": 0.14,
        "heldPercentInstitutions": 0.71, "marketCap": 6.4e12}


def test_input_uses_corrected_debt_equity_and_never_mutates_the_record():
    snap = fa._build_snapshot_from_info("NSE:INFY", INFY)
    rec = SimpleNamespace(symbol="NSE:INFY", cmp=1500.0, discovery={"avg_turnover_cr": 900.0}, layer2_pass=True,
                          entry=1480.0, stop_loss=1400.0, targets=[1700.0], setup="SMC_LONGTERM",
                          quality={"technical_score": 55.0}, final_selected=False, smc={"x": 1})
    before = copy.deepcopy(vars(rec))
    inp = ss.input_from_record(rec, snap, 62.0)
    assert vars(rec) == before
    assert inp.fundamental_score_de_fixed == snap.fundamental_score_de_fixed > snap.fundamental_score
    assert inp.quality == pytest.approx((snap.roce + snap.roe + snap.management_quality_de_fixed) / 3)
    assert inp.debt_equity_ratio == 0.0954 and inp.raw_debt_equity_legacy == 9.54


def test_hash_fundamentals_are_not_real():
    rec = SimpleNamespace(symbol="NSE:X", cmp=1.0, discovery={}, layer2_pass=True, entry=1.0, stop_loss=0.9,
                          targets=[], setup=None, quality={}, final_selected=False)
    assert ss.input_from_record(rec, fa._hash_snapshot("NSE:X"), None).fundamental_score_de_fixed is None


def test_nan_inputs_become_none():
    rec = SimpleNamespace(symbol="NSE:X", cmp=float("nan"), discovery={"avg_turnover_cr": float("inf")},
                          layer2_pass=True, entry=1.0, stop_loss=0.9, targets=[float("nan"), 2.0], setup=None,
                          quality={"technical_score": float("nan")}, final_selected=False)
    inp = ss.input_from_record(rec, None, float("nan"))
    assert inp.cmp is None and inp.avg_turnover_cr is None and inp.technical_score is None
    assert inp.targets == [2.0] and inp.smc_score is None


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    path = tmp_path / "shadow.db"

    def conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(store, "get_connection", conn)
    return conn


def test_persisted_run_carries_provenance_and_rerun_replaces(tmp_db, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    run = ss.select(_pool(), scan_id="VAL-LONGTERM-2026-09-28-x", date="2026-09-28", min_turnover_cr=1.0, top_n=12)
    assert store.write_run(run, ss.config(1.0)) == len(run.picks)
    assert store.write_run(run, ss.config(1.0)) == len(run.picks)
    c = tmp_db()
    meta = dict(c.execute("SELECT * FROM lt_shadow_runs").fetchone())
    assert meta["selector_version"] == "lt-shadow-v1" and meta["git_sha"] == "abc123"
    assert meta["budget"] == 8 and meta["overlap"] == run.overlap
    assert json.loads(meta["config"])["weights"] == ss.WEIGHTS
    rows = c.execute("SELECT * FROM lt_shadow_picks WHERE rank <= 12 ORDER BY rank").fetchall()
    assert len(rows) == 12 and sum(r["selected"] for r in rows) == 8
    assert json.loads(meta["coverage"])["support_with_fundamentals"] == run.eligible
    logged = c.execute("SELECT COUNT(*) FROM lt_shadow_picks").fetchone()[0]
    assert logged == len(run.picks)
    inputs = json.loads(rows[0]["inputs"])
    assert {"fundamental_score_de_fixed", "fundamental_score_legacy", "debt_equity_ratio"} <= set(inputs)


# ── End-to-end isolation: the live scan output is identical with the flag on or off ──

def _frame(seed: int, n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 200 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    return pd.DataFrame({
        "date": pd.bdate_range("2025-09-01", periods=n), "open": close * 0.995, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": rng.integers(200_000, 900_000, n).astype(float),
    })


def _run_scan(monkeypatch, horizon="LONGTERM", log_scan=True, as_of=None):
    import dashboard.backend.db as db
    import services.validation_engine as ve
    from services.universe_manager import UniverseSnapshot

    syms = [f"NSE:T{i:02d}" for i in range(12)]
    frames = {s: _frame(i) for i, s in enumerate(syms)}
    logged: list[list[dict]] = []

    async def fetch(symbols, *a, **k):
        return {s: _frame(99) for s in symbols}

    async def fund(symbols):
        return {s: fa._build_snapshot_from_info(s, {**INFY, "debtToEquity": 3.0 + 20 * i})
                for i, s in enumerate(symbols)}

    async def empty(symbols):
        return {}

    def levels(symbol, df, nifty):
        c = float(df["close"].iloc[-1])
        return (c * 0.97, c * 0.85, [c * 1.3], c * 1.5, [c * 0.95, c * 0.97], "SMC_LONGTERM_TEST", {"cmp": c})

    monkeypatch.setattr(ve, "load_nse_universe", lambda n: UniverseSnapshot(syms, n, len(syms), {"t": len(syms)}))
    monkeypatch.setattr(ve, "_fetch_frames", fetch)
    monkeypatch.setattr(ve, "scan_technical", empty)
    monkeypatch.setattr(ve, "analyze_fundamentals", fund)
    monkeypatch.setattr(ve, "analyze_news_sentiment", empty)
    monkeypatch.setattr(ve, "build_longterm_trade_levels", levels)
    monkeypatch.setattr(ve, "build_swing_trade_levels", lambda *a: None)
    monkeypatch.setattr(db, "log_signals_scan", lambda rows: logged.append(rows) or len(rows))
    res = asyncio.run(ve.run_validation_scan(horizon, top_k=5, symbols=syms, as_of=as_of,
                                             historical_frames=frames, log_scan=log_scan))
    strip = [{k: v for k, v in r.items() if k != "scan_id"} for r in (logged[0] if logged else [])]
    return res, strip


def _shadow_rows(tmp_db):
    try:
        return tmp_db().execute("SELECT COUNT(*) FROM lt_shadow_picks").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def test_live_scan_output_is_identical_with_shadow_on(monkeypatch, tmp_db):
    monkeypatch.delenv("LT_SHADOW_SELECTOR_ENABLED", raising=False)
    off_res, off_rows = _run_scan(monkeypatch)
    assert _shadow_rows(tmp_db) == 0

    monkeypatch.setenv("LT_SHADOW_SELECTOR_ENABLED", "1")
    on_res, on_rows = _run_scan(monkeypatch)
    assert on_rows == off_rows and len(on_rows) == 12
    assert [r.symbol for r in on_res.selected] == [r.symbol for r in off_res.selected]
    assert on_res.funnel.to_dict() == off_res.funnel.to_dict()
    assert _shadow_rows(tmp_db) > 0
    run = dict(tmp_db().execute("SELECT * FROM lt_shadow_runs").fetchone())
    assert run["scan_id"] == on_res.scan_id and run["date"] == on_res.records[0].date
    assert run["control_count"] == on_res.funnel.final_selected


def test_shadow_never_runs_for_swing_unlogged_or_historical_scans(monkeypatch, tmp_db):
    monkeypatch.setenv("LT_SHADOW_SELECTOR_ENABLED", "1")
    _run_scan(monkeypatch, horizon="SWING")
    _run_scan(monkeypatch, log_scan=False)
    _run_scan(monkeypatch, as_of="2026-09-25")   # backtest/replay: fundamentals are not point-in-time
    assert _shadow_rows(tmp_db) == 0


def test_shadow_failure_never_breaks_the_scan(monkeypatch, tmp_db):
    monkeypatch.delenv("LT_SHADOW_SELECTOR_ENABLED", raising=False)
    _, off_rows = _run_scan(monkeypatch)
    monkeypatch.setenv("LT_SHADOW_SELECTOR_ENABLED", "1")
    monkeypatch.setattr(ss, "select", lambda *a, **k: 1 / 0)
    res, rows = _run_scan(monkeypatch)
    assert rows == off_rows and res.logged_rows == 12


def test_report_compares_shadow_and_control_on_shared_labels(tmp_db):
    from scripts.lt_shadow_report import build_report

    c = tmp_db()
    c.executescript("""
        CREATE TABLE signals_log (scan_id TEXT, horizon TEXT, symbol TEXT, date TEXT, entry REAL, final_selected INT);
        CREATE TABLE forward_returns (symbol TEXT, date TEXT, base_close REAL, fwd_20d_pct REAL, excess_20d_pct REAL,
            fwd_60d_pct REAL, excess_60d_pct REAL, mae_5d_pct REAL, mae_20d_pct REAL);
    """)
    store.ensure_tables(c)
    d, sid = "2026-09-28", "VAL-LONGTERM-2026-09-28-a"
    c.executemany("INSERT INTO signals_log VALUES (?,?,?,?,?,?)", [
        (sid, "LONGTERM", "NSE:A", d, 95.0, 1), (sid, "LONGTERM", "NSE:B", d, 100.0, 1),
        (sid, "LONGTERM", "NSE:C", d, 100.0, 0), ("VAL-SWING-x", "SWING", "NSE:C", d, 100.0, 1),
    ])
    c.executemany("INSERT INTO forward_returns VALUES (?,?,?,?,?,?,?,?,?)", [
        ("NSE:A", d, 100.0, 4.0, 1.0, None, None, -6.0, -8.0),   # entry 5% below: triggers
        ("NSE:B", d, 100.0, -2.0, -3.0, None, None, -1.0, -1.0),
        ("NSE:C", d, 100.0, 10.0, 7.0, 20.0, 12.0, -1.0, -2.0),
    ])
    c.execute("INSERT INTO lt_shadow_runs (scan_id, date, selector_version, config, universe, eligible, budget, "
              "control_count, overlap, exclusions) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (sid, d, "lt-shadow-v1", "{}", 3, 3, 2, 2, 1, "{}"))
    c.executemany("INSERT INTO lt_shadow_picks (scan_id, date, symbol, rank, score, selected, control_selected, "
                  "entry, components, inputs) VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (sid, d, "NSE:C", 1, 0.9, 1, 0, 100.0, "{}", "{}"), (sid, d, "NSE:A", 2, 0.8, 1, 1, 95.0, "{}", "{}"),
        (sid, d, "NSE:B", 3, 0.1, 0, 1, 100.0, "{}", "{}"),
    ])
    c.commit()
    rep = build_report(c)
    assert rep["runs"] == 1 and rep["per_run"][0]["overlap"] == 1
    assert rep["latest_differences"] == {"scan_id": sid, "shadow_only": ["NSE:C"], "control_only": ["NSE:B"]}
    assert rep["swing_lt_overlap"]["shadow_picks_also_swing_final"] == 1
    assert rep["swing_lt_overlap"]["control_picks_also_swing_final"] == 0
    fr = rep["forward_returns"]
    assert fr["shadow"]["20d"]["mean_fwd"] == 7.0 and fr["control"]["20d"]["mean_fwd"] == 1.0
    assert fr["shadow"]["60d"]["n_labelled"] == 1 and fr["control"]["60d"]["n_labelled"] == 0
    assert rep["trigger_expiry_approx"]["control"]["triggered_5d_pct"] == 100.0
    assert fr["control_shadow_eligible"]["20d"]["n"] == 2 and fr["control_shadow_ineligible"]["20d"]["n"] == 0
    assert fr["control"]["20d"]["distinct_symbols"] == 2
    assert rep["per_run"][0]["control_shadow_eligible"] == 2 and rep["per_run"][0]["coverage"] is None


def test_config_snapshots_control_flags_from_an_allowlist(monkeypatch):
    monkeypatch.setenv("EXCEPTIONALISM_ENABLED", "1")
    monkeypatch.setenv("KITE_API_SECRET", "must-not-appear")
    monkeypatch.delenv("FUND_DE_UNIT_FIX", raising=False)
    cfg = ss.config(1.0)
    assert cfg["control_env"]["EXCEPTIONALISM_ENABLED"] == "1"
    assert cfg["control_env"]["FUND_DE_UNIT_FIX"] is None
    assert "must-not-appear" not in json.dumps(cfg)
    assert not any(w in k for k in ss.CONTROL_ENV_KEYS for w in ("SECRET", "TOKEN", "KEY", "PASSWORD"))
