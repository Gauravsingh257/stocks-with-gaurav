"""Static portfolio routes must not be swallowed by /{position_id}.

FastAPI matches routes in definition order, so any static path declared AFTER
the dynamic "/{position_id}" route gets parsed as a position id and 422s.
`/api/portfolio/top-running` shipped that way and was dead on arrival in
production while every other route looked healthy — the failure is invisible
until someone requests that exact path.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="route_order_")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.db.schema import init_db  # noqa: E402
from dashboard.backend.db.portfolio import init_portfolio_db  # noqa: E402
from dashboard.backend.routes.portfolio import router  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_db()
    init_portfolio_db()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# Every non-parameterised GET under /api/portfolio. A 422 here means the path
# was matched as a position id.
STATIC_GETS = [
    "/api/portfolio/summary",
    "/api/portfolio/swing",
    "/api/portfolio/longterm",
    "/api/portfolio/counts",
    "/api/portfolio/top-running",
    "/api/portfolio/journal/all",
    "/api/portfolio/journal/stats",
    "/api/portfolio/journal/dedupe-audit",
    "/api/portfolio/performance/consistency",
]


@pytest.mark.parametrize("path", STATIC_GETS)
def test_static_route_is_not_matched_as_a_position_id(client, path):
    r = client.get(path)
    assert r.status_code != 422, (
        f"{path} returned 422 — it is declared after /{{position_id}} and is "
        f"being parsed as a position id. Move it above that route."
    )
    assert r.status_code < 500, f"{path} returned {r.status_code}"


def test_top_running_returns_the_expected_shape(client):
    r = client.get("/api/portfolio/top-running?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total_open" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= 5


def test_position_id_route_still_works_for_a_real_integer(client):
    """Moving the static route must not break the dynamic one."""
    r = client.get("/api/portfolio/999999")
    assert r.status_code == 404, "an unknown integer id should 404, not 422"
