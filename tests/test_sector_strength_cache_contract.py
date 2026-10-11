"""Regression: the sector:strength cache must be deterministic, not last-writer-wins.

2026-10-11/12 audit. Three producers write the same day-scoped key:
  constituent (~99% coverage) > canonical_index (~46%) > legacy_index (~13%, orphan keys)

`ranking_engine` runs BEFORE the validation scan every day (03:02 vs 03:04 UTC), so
it was always the cold-cache caller reading 13% bands while every later consumer read
99% bands — the same engine, same day, different inputs (LONGTERM 2026-10-11:
00:02 selected 0, 00:04 selected 1).

The contract: a lower-confidence source may never overwrite a higher-confidence one
for the same day, the write is atomic, and provenance is preserved.
"""

import json

import pytest

import services.sector_strength as ss


class FakeRedis:
    """Enough Redis to exercise the compare-and-set script, including contention."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.evals = 0

    def get(self, k):
        return self.store.get(k)

    def eval(self, script, numkeys, key, payload, rank, day, ttl, banded):
        # Mirror _CAS_LUA exactly.
        self.evals += 1
        cur = self.store.get(key)
        if cur:
            try:
                prev = json.loads(cur)
                p = (prev or {}).get("_provenance")
                if p and p.get("generated_for") == day:
                    if int(p.get("rank", 0)) > int(rank):
                        return 0
                    if int(banded) == 0 and int(p.get("banded_sectors", 0)) > 0:
                        return 0
            except Exception:
                pass
        self.store[key] = payload
        return 1


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    import dashboard.backend.cache as cache
    monkeypatch.setattr(cache, "_get_redis", lambda: r)
    monkeypatch.setattr(cache, "get", lambda k: json.loads(r.store[k]) if k in r.store else None)
    return r


def _result(sectors: dict, day: str = "2026-10-12") -> dict:
    return {"generated_for": day, "sectors": sectors, "leading": []}


CONSTITUENT = _result({f"S{i}": {"band": "neutral"} for i in range(20)})
LEGACY = _result({"Banking": {"band": "leading"}, "Finance": {"band": "unknown"}})


def test_ranking_order_no_longer_decides_the_day(redis):
    """The real incident: legacy lands first, constituent second, legacy must not return."""
    assert ss.cache_put(dict(LEGACY), ss.SOURCE_LEGACY_INDEX) is True
    assert ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT) is True
    # A later cold caller recomputing the legacy path must now be REFUSED.
    assert ss.cache_put(dict(LEGACY), ss.SOURCE_LEGACY_INDEX) is False
    assert ss.sector_strength_status()["source"] == ss.SOURCE_CONSTITUENT


def test_higher_confidence_always_wins_regardless_of_arrival_order(redis):
    for first, second in [
        (ss.SOURCE_LEGACY_INDEX, ss.SOURCE_CONSTITUENT),
        (ss.SOURCE_CANONICAL_INDEX, ss.SOURCE_CONSTITUENT),
        (ss.SOURCE_LEGACY_INDEX, ss.SOURCE_CANONICAL_INDEX),
    ]:
        redis.store.clear()
        ss.cache_put(_result({"A": {"band": "neutral"}}), first)
        ss.cache_put(_result({"B": {"band": "neutral"}}), second)
        assert ss.sector_strength_status()["source"] == second
        # ...and the loser cannot take it back.
        assert ss.cache_put(_result({"A": {"band": "neutral"}}), first) is False


def test_same_source_may_refresh_itself(redis):
    """Equal rank must still store, or the data could never be updated."""
    ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT)
    fresher = _result({f"S{i}": {"band": "leading"} for i in range(20)})
    assert ss.cache_put(fresher, ss.SOURCE_CONSTITUENT) is True
    assert ss.sector_strength_status()["leading"] == []


def test_new_day_resets_precedence(redis):
    ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT)
    tomorrow = _result({"Finance": {"band": "unknown"}}, day="2026-10-13")
    assert ss.cache_put(tomorrow, ss.SOURCE_LEGACY_INDEX) is True


def test_concurrent_writers_resolve_deterministically(redis):
    """Interleaved writers must converge on the best source, whatever the order."""
    import itertools
    for order in itertools.permutations(
        [ss.SOURCE_LEGACY_INDEX, ss.SOURCE_CANONICAL_INDEX, ss.SOURCE_CONSTITUENT]
    ):
        redis.store.clear()
        for src in order:
            ss.cache_put(_result({"A": {"band": "neutral"}}), src)
        assert ss.sector_strength_status()["source"] == ss.SOURCE_CONSTITUENT


def test_provenance_records_coverage_and_source(redis):
    ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT)
    st = ss.sector_strength_status()
    assert st["coverage_pct"] == 100.0 and st["emitted_sectors"] == 20
    assert st["rank"] == ss.SOURCE_RANK[ss.SOURCE_CONSTITUENT]
    assert st["age_sec"] is not None

    redis.store.clear()
    ss.cache_put(dict(LEGACY), ss.SOURCE_LEGACY_INDEX)
    st = ss.sector_strength_status()
    # One of the two legacy sectors is "unknown" -> 50% coverage, visibly poor.
    assert st["coverage_pct"] == 50.0 and st["source"] == ss.SOURCE_LEGACY_INDEX


def test_corrupt_stored_value_does_not_block_a_write(redis):
    redis.store[ss._redis_key()] = "{not json"
    assert ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT) is True


def test_redis_down_falls_back_without_raising(monkeypatch):
    import dashboard.backend.cache as cache
    monkeypatch.setattr(cache, "_get_redis", lambda: None)
    written = {}
    monkeypatch.setattr(cache, "set", lambda k, v, ttl_seconds=0: written.update({k: v}) or True)
    assert ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT) is True
    assert written, "in-memory fallback must still store"


def test_cache_put_never_raises(monkeypatch):
    import dashboard.backend.cache as cache

    def boom():
        raise RuntimeError("redis exploded")
    monkeypatch.setattr(cache, "_get_redis", boom)
    assert ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT) is False


def test_status_reports_unavailable_when_nothing_cached(redis):
    assert ss.sector_strength_status()["available"] is False


def test_blackout_cannot_erase_a_good_day(redis):
    """A market-data blackout yields zero bands; it must NOT wipe real ones.

    Found by this test: equal rank alone was not enough, because the constituent
    path re-runs on a degraded scan and would have cleared the day.
    """
    ss.cache_put(dict(CONSTITUENT), ss.SOURCE_CONSTITUENT)
    assert ss.cache_put(_result({}), ss.SOURCE_CONSTITUENT) is False
    st = ss.sector_strength_status()
    assert st["coverage_pct"] == 100.0 and st["emitted_sectors"] == 20


def test_blackout_stores_when_nothing_better_exists(redis):
    """With an empty day, an empty result is still allowed through (honest 0%)."""
    assert ss.cache_put(_result({}), ss.SOURCE_CONSTITUENT) is True
