"""Fundamentals coverage fix: failed requests are never cached as data, and the paced
pre-warm fills the cache deterministically without touching any scoring."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from services import fundamental_analysis as fa
from services import fundamentals_prewarm as pw

GOOD = {"trailingPE": 20.0, "returnOnEquity": 0.18, "revenueGrowth": 0.1, "earningsGrowth": 0.12,
        "priceToBook": 3.0, "debtToEquity": 40.0, "marketCap": 5e11, "sector": "Industrials"}
EMPTY = {"symbol": "X.NS", "longName": "X Ltd", "currentPrice": 10.0}   # answered, no fundamentals


class RateLimited(Exception):
    pass


RateLimited.__name__ = "YFRateLimitError"


@pytest.fixture()
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "_CACHE_DIR", tmp_path)
    return tmp_path


def _provider(monkeypatch, answers: dict, calls: list | None = None):
    """answers: symbol -> dict | Exception class (raised inside yfinance)."""
    def fake(symbol):
        if calls is not None:
            calls.append(symbol)
        a = answers[symbol]
        if isinstance(a, type) and issubclass(a, Exception):
            try:
                raise a("Too Many Requests")
            except Exception as exc:
                return fa.FetchFailed(type(exc).__name__)
        return dict(a)
    monkeypatch.setattr(fa, "_fetch_yf_info", fake)


# ── classification ────────────────────────────────────────────────────────────

def test_fetch_status_separates_failure_from_no_data():
    assert fa.fetch_status(fa.FetchFailed("YFRateLimitError")) == "transient"
    assert fa.fetch_status({}) == "transient"
    assert fa.fetch_status(EMPTY) == "empty"
    assert fa.fetch_status(GOOD) == "ok"


def test_provider_exception_becomes_a_marked_empty_dict(monkeypatch):
    import yfinance

    class T:
        def __init__(self, *_):
            pass

        @property
        def info(self):
            raise RateLimited("Too Many Requests")

    monkeypatch.setattr(yfinance, "Ticker", T)
    info = fa._fetch_yf_info("NSE:ABC")
    assert info == {} and isinstance(info, fa.FetchFailed) and info.reason == "YFRateLimitError"


# ── caching policy ────────────────────────────────────────────────────────────

def test_flag_off_keeps_legacy_caching_of_failures(monkeypatch, cache):
    monkeypatch.delenv("FUND_COVERAGE_FIX", raising=False)
    calls: list = []
    _provider(monkeypatch, {"NSE:A": RateLimited}, calls)
    assert fa._fetch_snapshot("NSE:A").data_source == "hash"
    assert fa._fetch_snapshot("NSE:A").data_source == "hash"
    assert calls == ["NSE:A"]                       # legacy: the failure was cached for the day


def test_flag_on_never_caches_a_failed_request(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    calls: list = []
    answers = {"NSE:A": RateLimited}
    _provider(monkeypatch, answers, calls)
    assert fa._fetch_snapshot("NSE:A").data_source == "hash"   # same placeholder as before …
    assert fa.cache_age_hours("NSE:A") is None                 # … but nothing was stored
    answers["NSE:A"] = GOOD
    snap = fa._fetch_snapshot("NSE:A")                         # next caller retries and gets real data
    assert snap.data_source == "yfinance" and calls == ["NSE:A", "NSE:A"]


def test_flag_on_still_caches_genuine_empties_and_real_data(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    calls: list = []
    _provider(monkeypatch, {"NSE:E": EMPTY, "NSE:G": GOOD}, calls)
    for _ in range(2):
        fa._fetch_snapshot("NSE:E")
        fa._fetch_snapshot("NSE:G")
    assert calls == ["NSE:E", "NSE:G"]              # no unnecessary repeat calls


def test_scores_are_identical_with_and_without_the_flag(monkeypatch, cache, tmp_path):
    _provider(monkeypatch, {"NSE:G": GOOD})
    monkeypatch.delenv("FUND_COVERAGE_FIX", raising=False)
    off = fa.refresh_snapshot("NSE:G")[0]
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    on = fa.refresh_snapshot("NSE:G")[0]
    assert off == on


def test_phase0_consumer_reads_the_warm_cache_without_calling_the_provider(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    monkeypatch.setenv("PHASE0_NO_SYNTHETIC", "1")
    _provider(monkeypatch, {"NSE:G": GOOD, "NSE:E": EMPTY})
    pw.prewarm(["NSE:G", "NSE:E"], rpm=6000, workers=1, retry_passes=0, cooldown_s=0)
    _provider(monkeypatch, {}, None)                # any provider call would now KeyError
    out = asyncio.run(fa.analyze_fundamentals(["NSE:G", "NSE:E"]))
    assert set(out) == {"NSE:G"} and out["NSE:G"].data_source == "yfinance"


# ── pacing and ordering ───────────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def sleep(self, s):
        with self.lock:
            self.t += s


def test_pacer_caps_the_rate_and_honours_cooldown():
    c = FakeClock()
    p = pw.Pacer(60, c.now, c.sleep)          # one start per second
    for _ in range(5):
        p.wait()
    assert c.now() == pytest.approx(4.0)
    p.cooldown(300)
    p.wait()
    assert c.now() >= 304.0


def test_order_is_deterministic_and_not_alphabetical():
    syms = [f"NSE:{chr(65 + i)}{j}" for i in range(26) for j in range(10)]
    a = pw.prewarm_order(syms, "2026-09-28")
    assert a == pw.prewarm_order(list(reversed(syms)), "2026-09-28")
    assert a != sorted(syms) and a != pw.prewarm_order(syms, "2026-09-29")
    first_fifth = a[: len(a) // 5]
    assert len({s[4] for s in first_fifth}) > 15    # the head spans most of the alphabet


def test_prewarm_retries_rate_limited_symbols_after_cooldown(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    c = FakeClock()
    answers = {"NSE:A": GOOD, "NSE:B": RateLimited, "NSE:C": GOOD}
    attempts: list = []

    def fake(symbol):
        attempts.append(symbol)
        if symbol == "NSE:B" and attempts.count("NSE:B") == 1:
            return fa.FetchFailed("YFRateLimitError")
        return dict(GOOD)

    monkeypatch.setattr(fa, "_fetch_yf_info", fake)
    rep = pw.prewarm(list(answers), rpm=60, workers=1, retry_passes=2, cooldown_s=300,
                     clock=c.now, sleep=c.sleep, day="d")
    assert rep["status"] == {"ok": 3} and rep["rate_limit_responses"] == 1
    assert rep["passes"] == [{"attempted": 3, "transient_failures": 1}, {"attempted": 1, "transient_failures": 0}]
    assert all(fa.cache_age_hours(s) is not None for s in answers)


def test_prewarm_never_writes_a_symbol_that_keeps_failing(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    c = FakeClock()
    monkeypatch.setattr(fa, "_fetch_yf_info", lambda s: fa.FetchFailed("HTTPError"))
    rep = pw.prewarm(["NSE:A"], rpm=60, workers=1, retry_passes=2, cooldown_s=10, clock=c.now, sleep=c.sleep)
    assert rep["status"] == {"transient": 1} and rep["still_failed"] == ["NSE:A"]
    assert fa.cache_age_hours("NSE:A") is None and len(rep["passes"]) == 3


def test_prewarm_stops_at_the_deadline(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    c = FakeClock()
    _provider(monkeypatch, {f"NSE:S{i}": GOOD for i in range(100)})
    rep = pw.prewarm([f"NSE:S{i}" for i in range(100)], rpm=60, workers=1, retry_passes=0,
                     deadline_s=30, clock=c.now, sleep=c.sleep)
    assert rep["stopped_at_deadline"] and rep["requests"] <= 31
    assert rep["status"].get("not_attempted", 0) >= 69


def test_prewarm_skips_fresh_entries(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    calls: list = []
    _provider(monkeypatch, {"NSE:A": GOOD, "NSE:B": GOOD}, calls)
    fa._fetch_snapshot("NSE:A")
    rep = pw.prewarm(["NSE:A", "NSE:B"], rpm=6000, workers=1, retry_passes=0, max_age_h=12)
    assert rep["already_fresh"] == 1 and calls == ["NSE:A", "NSE:B"]


def test_prewarm_concurrency_is_bounded(monkeypatch, cache):
    monkeypatch.setenv("FUND_COVERAGE_FIX", "1")
    live = peak = 0
    lock = threading.Lock()

    def fake(symbol):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.01)
        with lock:
            live -= 1
        return dict(GOOD)

    monkeypatch.setattr(fa, "_fetch_yf_info", fake)
    pw.prewarm([f"NSE:S{i}" for i in range(40)], rpm=60000, workers=3, retry_passes=0)
    assert 1 <= peak <= 3


# ── the script's guards ───────────────────────────────────────────────────────

def test_script_stop_time_and_quintiles():
    from datetime import datetime

    from scripts.prewarm_fundamentals import IST, alphabetical_quintiles, seconds_until_stop

    assert seconds_until_stop(datetime(2026, 9, 28, 6, 45, tzinfo=IST), "08:15") == 90 * 60
    assert seconds_until_stop(datetime(2026, 9, 28, 8, 20, tzinfo=IST), "08:15") == 0
    syms = [f"S{i:02d}" for i in range(10)]
    assert alphabetical_quintiles(syms, set(syms[:4])) == [1.0, 1.0, 0.0, 0.0, 0.0]


def test_runner_job_is_flag_gated_with_a_short_misfire_grace():
    import inspect

    import agents.runner as runner

    src = inspect.getsource(runner)
    block = src[src.index("def _run_fundamentals_prewarm"): src.index('id="fundamentals_prewarm"') + 200]
    assert "coverage_fix_enabled()" in block and "misfire_grace_time=15 * 60" in block
    assert 'hour=6, minute=45, day_of_week="mon-fri"' in block
