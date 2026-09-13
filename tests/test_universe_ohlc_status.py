"""snapshot_status must tell a manifest apart from the data behind it.

On 2026-09-07 the manifest and `latest` pointer (7-day TTL) were alive while
every price shard (50h TTL) had expired. `available` said True, the loader
returned {} without an error, and the ranking engine passed 0 stocks all day.
These tests pin that state so a snapshot can never again look healthy while
holding no bars.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="data_health_"))

from services import universe_ohlc as u  # noqa: E402


class _Pipe:
    def __init__(self, store):
        self.store, self.keys = store, []

    def ttl(self, key):
        self.keys.append(key)
        return self

    def execute(self):
        return [self.store.ttl(k) for k in self.keys]


class _FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def put(self, key, value, ttl):
        self.kv[key], self.ttls[key] = value, ttl

    def get(self, key):
        return self.kv.get(key)

    def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    def ttl(self, key):
        return self.ttls[key] if key in self.kv else -2

    def pipeline(self, transaction=False):
        return _Pipe(self)


def _publish(fake, *, day="2026-09-04", shards=9, alive=None, shard_ttl=20 * 3600):
    manifest = {"day": day, "symbols": 2142, "shards": shards, "shard_size": 250,
                "index": {"RELIANCE": 0}, "bytes": 13_921_500, "written_at": time.time() - 3600}
    fake.put(u._latest_key(), day, 7 * 86400)
    fake.put(u._manifest_key(day), json.dumps(manifest), 7 * 86400)
    for n in range(shards if alive is None else alive):
        fake.put(u._shard_key(day, n), "blob", shard_ttl)


def _with(monkeypatch, fake):
    monkeypatch.setattr(u, "_get_redis", lambda: fake)


def test_all_shards_alive_is_usable(monkeypatch):
    fake = _FakeRedis()
    _publish(fake)
    _with(monkeypatch, fake)
    s = u.snapshot_status()
    assert s["available"] is True and s["usable"] is True
    assert s["shards_alive"] == 9
    assert s["shard_ttl_hours"] == 20.0
    assert abs(s["expires_at"] - (time.time() + 20 * 3600)) < 5


def test_manifest_without_shards_is_not_usable(monkeypatch):
    """The 2026-09-07 state."""
    fake = _FakeRedis()
    _publish(fake, alive=0)
    _with(monkeypatch, fake)
    s = u.snapshot_status()
    assert s["available"] is True          # the old field alone looked healthy...
    assert s["usable"] is False            # ...this one tells the truth
    assert s["shards_alive"] == 0 and s["expires_at"] is None
    # and the loader really does go silent in exactly this state
    assert u.load_universe_ohlc(["RELIANCE"]) == {}


def test_partial_shards_are_not_usable(monkeypatch):
    fake = _FakeRedis()
    _publish(fake, alive=3)
    _with(monkeypatch, fake)
    s = u.snapshot_status()
    assert s["usable"] is False and s["shards_alive"] == 3


def test_no_manifest_and_no_redis(monkeypatch):
    _with(monkeypatch, _FakeRedis())
    assert u.snapshot_status() == {"available": False, "usable": False, "reason": "no_snapshot"}
    monkeypatch.setattr(u, "_get_redis", lambda: None)
    assert u.snapshot_status()["usable"] is False


def test_data_health_route_serves_the_status(monkeypatch):
    """Mounted on the real research router, so a route-order collision (a
    dynamic path declared earlier swallowing this one) fails here, not in prod."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dashboard.backend.routes.research import router

    monkeypatch.setattr(u, "snapshot_status", lambda day=None: {
        "available": True, "usable": False, "shards": 9, "shards_alive": 0})
    monkeypatch.setattr(u, "kite_ohlc_enabled", lambda: True)
    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/api/research/data-health")
    assert resp.status_code == 200
    body = resp.json()["universe_ohlc"]
    assert body["enabled"] is True and body["usable"] is False and body["shards_alive"] == 0
