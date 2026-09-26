"""Index OHLC snapshot (sector-rotation Phase 0).

Pins the properties that matter: a snapshot missing a benchmark is refused, the
data round-trips, status reports a vanished data key as unusable, and indices
never enter the `ohlc:universe:*` namespace the ranking engine reads.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="index_ohlc_"))

from services import index_ohlc as ix  # noqa: E402


class _Pipe:
    def __init__(self, store):
        self.store, self.ops = store, []

    def setex(self, key, ttl, value):
        self.ops.append((key, ttl, value))
        return self

    def execute(self):
        for key, ttl, value in self.ops:
            self.store.setex(key, ttl, value)
        return [True] * len(self.ops)


class _FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key, ttl, value):
        self.kv[key], self.ttls[key] = value, ttl

    def get(self, key):
        return self.kv.get(key)

    def ttl(self, key):
        return self.ttls[key] if key in self.kv else -2

    def pipeline(self, transaction=False):
        return _Pipe(self)


def _bars(n=3, start=100.0):
    return [{"date": f"2026-09-{22 + i:02d}", "open": start, "high": start + 1, "low": start - 1,
             "close": start + i, "volume": 0} for i in range(n)]


def _full():
    return {name: _bars() for name, _, _ in ix.INDEX_REGISTRY}


def _patch(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ix, "_get_redis", lambda: fake)
    return fake


def test_refuses_snapshot_without_benchmark(monkeypatch):
    fake = _patch(monkeypatch)
    candles = _full()
    candles.pop("NIFTY 500")
    out = ix.publish_index_ohlc(candles, day="2026-09-26")
    assert out["written"] is False and out["missing_required"] == ["NIFTY 500"]
    assert fake.kv == {}


def test_refuses_thin_snapshot(monkeypatch):
    _patch(monkeypatch)
    candles = {n: _bars() for n in ix.REQUIRED}
    assert ix.publish_index_ohlc(candles, day="2026-09-26")["written"] is False


def test_round_trip_and_status(monkeypatch):
    fake = _patch(monkeypatch)
    candles = _full()
    candles.pop("NIFTY CHEMICALS")  # one missing sector index is tolerated and reported
    out = ix.publish_index_ohlc(candles, day="2026-09-26")
    assert out["written"] is True and out["missing"] == ["NIFTY CHEMICALS"]

    loaded = ix.load_index_ohlc(["NIFTY 50", "NIFTY IT"])
    assert set(loaded) == {"NIFTY 50", "NIFTY IT"}
    assert [b["close"] for b in loaded["NIFTY 50"]] == [100.0, 101.0, 102.0]

    st = ix.index_snapshot_status()
    assert st["usable"] is True and st["day"] == "2026-09-26"
    assert st["series"]["NIFTY 50"] == {"bars": 3, "first": "2026-09-22", "last": "2026-09-24"}
    assert all(t == ix.TTL_SEC for t in fake.ttls.values())
    assert not any(k.startswith("ohlc:universe") for k in fake.kv)


def test_status_unusable_when_data_expired(monkeypatch):
    fake = _patch(monkeypatch)
    ix.publish_index_ohlc(_full(), day="2026-09-26")
    del fake.kv[ix._data_key("2026-09-26")]
    st = ix.index_snapshot_status()
    assert st["available"] is True and st["usable"] is False
    assert ix.load_index_ohlc() == {}


def test_refresh_uses_fetcher_and_reports_failures(monkeypatch):
    _patch(monkeypatch)

    class _Fetcher:
        def load_index_instruments(self):
            return 136

        def fetch_index(self, name, interval, lookback):
            assert interval == "day" and lookback == 30
            return None if name == "NIFTY MEDIA" else _bars()

    out = ix.refresh_index_ohlc(fetcher=_Fetcher(), lookback_days=30)
    assert out["written"] is True
    assert out["failed"] == ["NIFTY MEDIA"] and out["fetched"] == len(ix.INDEX_REGISTRY) - 1


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("SECTOR_INDEX_OHLC_ENABLED", raising=False)
    assert ix.index_ohlc_enabled() is False
