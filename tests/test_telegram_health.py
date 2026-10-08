"""Regression: Telegram delivery is judged by Telegram's response, and a dead
destination is observable (2026-09-29 → 10-08 outage: every channel send
returned PEER_ID_INVALID and nothing recorded it)."""

import time

import pytest

import services.telegram_health as th
from scripts.telegram_health_check import verdict


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def op(*a, **k):
            self.ops.append((name, a, k))
            return self
        return op

    def execute(self):
        for name, a, k in self.ops:
            getattr(self.r, name)(*a, **k)


class FakeRedis:
    def __init__(self):
        self.h, self.s, self.l = {}, {}, {}

    def pipeline(self, transaction=True):
        return FakePipe(self)

    def sadd(self, k, v):
        self.s.setdefault(k, set()).add(v)

    def smembers(self, k):
        return self.s.get(k, set())

    def hset(self, k, mapping):
        self.h.setdefault(k, {}).update({a: str(b) for a, b in mapping.items()})

    def hincrby(self, k, f, n):
        d = self.h.setdefault(k, {})
        d[f] = str(int(d.get(f, 0)) + n)

    def hgetall(self, k):
        return dict(self.h.get(k, {}))

    def rpush(self, k, v):
        self.l.setdefault(k, []).append(v)

    def lrange(self, k, a, b):
        return self.l.get(k, [])[a:]

    def ltrim(self, *a):
        pass

    def expire(self, *a):
        pass


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(th, "_get_redis", lambda: r)
    return r


def test_success_requires_http_200_and_ok_true():
    assert th.classify_response(200, {"ok": True, "result": {"message_id": 7}}).ok
    assert th.classify_response(200, {"ok": True, "result": {"message_id": 7}}).message_id == 7
    assert not th.classify_response(200, {"ok": False, "description": "x"}).ok
    assert not th.classify_response(200, None).ok


def test_failure_carries_telegram_error_text():
    res = th.result_from_response(Resp(400, {"ok": False, "error_code": 400,
                                             "description": "Bad Request: PEER_ID_INVALID"}))
    assert not res.ok and res.error == "Bad Request: PEER_ID_INVALID"


def test_send_message_records_failure_not_sent(monkeypatch, redis):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(400, {"ok": False,
                        "description": "Bad Request: PEER_ID_INVALID"}))
    res = th.send_message("tok", "-1003268636791", "hi", source="test")
    assert not res.ok
    h = redis.hgetall(th.DEST_KEY_PREFIX + "-1003268636791")
    assert h["consecutive_failures"] == "1" and "PEER_ID_INVALID" in h["last_error"]
    assert "last_ok_ts" not in h


def test_send_message_never_raises_on_network_error(monkeypatch, redis):
    import requests

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(requests, "post", boom)
    res = th.send_message("tok", "123", "hi", source="test")
    assert not res.ok and "ConnectionError" in res.error


def test_destination_becomes_failing_and_recovers(redis):
    chat = "-1003268636791"
    bad = th.SendResult(ok=False, status_code=400, error="Bad Request: PEER_ID_INVALID")
    th.record_send_result("engine", chat, bad)
    assert th.get_health()["destinations"][0]["status"] == "DEGRADED"
    th.record_send_result("engine", chat, bad)
    h = th.get_health()
    assert h["ok"] is False and h["destinations"][0]["status"] == "FAILING"
    assert h["destinations"][0]["chat"] != chat  # masked
    assert verdict(h)[0] == 1
    time.sleep(0.01)
    th.record_send_result("engine", chat, th.SendResult(ok=True, status_code=200))
    h = th.get_health()
    assert h["ok"] is True and h["destinations"][0]["status"] == "OK"
    assert verdict(h)[0] == 0


def test_check_script_fails_when_health_unavailable():
    assert verdict({"available": False, "reason": "redis_unavailable"})[0] == 2
    assert verdict({"available": True, "ok": True, "failing": [], "destinations": []})[0] == 0
