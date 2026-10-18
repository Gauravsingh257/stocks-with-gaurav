"""`engine_version` / `engine_started_at` must survive a long-running engine.

Both were written once on lock acquisition with a 24h TTL and never refreshed.
A healthy engine runs for days, so in steady state the keys expired and the
dashboard lost the running version and uptime — observed 2026-10-17, when
`engine_version` read as absent after ~24h with no restart and only reappeared
because a deploy restarted the process. The value was never wrong; it vanished,
which is worse during an incident.

These pin the refresh-ahead behaviour and, just as importantly, the three things
it must NOT do: refresh without the lock, invent a value, or burn a Redis call
on every renewer tick.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="engine_identity_"))

import engine_runtime as er  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []

    def set(self, k, v, ex=None, nx=False, px=None):
        self.calls.append(("set", k))
        if nx and k in self.store:
            return None
        self.store[k] = str(v)
        if ex:
            self.ttls[k] = ex
        return True

    def get(self, k):
        self.calls.append(("get", k))
        return self.store.get(k)

    def ttl(self, k):
        self.calls.append(("ttl", k))
        return self.ttls.get(k, -2)

    def expire(self, k, ex):
        self.ttls[k] = ex
        return 1


def _wire(monkeypatch, *, holder=True):
    fake = FakeRedis()
    monkeypatch.setattr(er, "_get_redis", lambda: fake)
    monkeypatch.setattr(er, "_lock_holder", holder, raising=False)
    monkeypatch.setattr(er, "_engine_version_value", "v4.2.1", raising=False)
    monkeypatch.setattr(er, "_engine_started_at_value", "1792270785.9", raising=False)
    return fake


def test_an_expired_version_key_is_rewritten(monkeypatch):
    fake = _wire(monkeypatch)
    assert fake.get("engine_version") is None       # gone, as it was in production
    er.refresh_identity_keys()
    assert fake.store["engine_version"] == "v4.2.1"
    assert fake.ttls["engine_version"] == er._IDENTITY_TTL_SEC
    assert fake.store["engine_started_at"] == "1792270785.9"


def test_a_key_near_expiry_is_extended(monkeypatch):
    fake = _wire(monkeypatch)
    fake.set("engine_version", "v4.2.1", ex=100)     # about to lapse
    er.refresh_identity_keys()
    assert fake.ttls["engine_version"] == er._IDENTITY_TTL_SEC


def test_a_healthy_ttl_is_left_alone(monkeypatch):
    """Refresh-ahead, not refresh-always: a 24h key must not cost a write per tick."""
    fake = _wire(monkeypatch)
    fake.set("engine_version", "v4.2.1", ex=er._IDENTITY_TTL_SEC)
    fake.set("engine_started_at", "1792270785.9", ex=er._IDENTITY_TTL_SEC)
    fake.calls.clear()
    er.refresh_identity_keys()
    assert not [c for c in fake.calls if c[0] == "set"], "should not rewrite a fresh key"
    assert fake.ttls["engine_version"] == er._IDENTITY_TTL_SEC


def test_nothing_is_written_without_the_lock(monkeypatch):
    """A standby or dead instance must never resurrect another engine's identity."""
    fake = _wire(monkeypatch, holder=False)
    er.refresh_identity_keys()
    assert fake.store == {}


def test_an_unknown_value_is_never_invented(monkeypatch):
    """Before set_engine_version runs there is nothing to restore — stay silent."""
    fake = _wire(monkeypatch)
    monkeypatch.setattr(er, "_engine_version_value", None, raising=False)
    monkeypatch.setattr(er, "_engine_started_at_value", None, raising=False)
    er.refresh_identity_keys()
    assert fake.store == {}


def test_set_engine_version_remembers_the_value_for_the_refresher(monkeypatch):
    fake = _wire(monkeypatch)
    monkeypatch.setattr(er, "_engine_version_value", None, raising=False)
    er.set_engine_version("v9.9.9")
    assert er._engine_version_value == "v9.9.9"
    assert fake.store["engine_version"] == "v9.9.9"


def test_a_redis_outage_is_not_fatal(monkeypatch):
    """The renewer thread calls this every tick; it must never raise."""
    monkeypatch.setattr(er, "_get_redis", lambda: None)
    monkeypatch.setattr(er, "_lock_holder", True, raising=False)
    er.refresh_identity_keys()          # no exception

    class Broken(FakeRedis):
        def ttl(self, k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(er, "_get_redis", lambda: Broken())
    monkeypatch.setattr(er, "_engine_version_value", "v4.2.1", raising=False)
    er.refresh_identity_keys()          # swallowed, logged at debug
