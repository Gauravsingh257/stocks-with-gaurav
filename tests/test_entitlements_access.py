"""
A1 — public preview vs full access.

The product has no paid plan: no billing system exists and every production
account carries role FREE. Four call sites nevertheless gated on
`role in ("PREMIUM", "ADMIN")`, so a signed-in user was served the anonymous
payload — a restriction that protected nothing and advertised a tier that
cannot be bought.

The rule these tests pin down:

    anonymous  -> capped PREVIEW, built on the server
    signed in  -> the full product

and the security property that matters more than either: **a preview must not
contain what it is hiding.** A blurred UI is a presentation choice; the
truncation has to happen before the response is serialised.
"""
from __future__ import annotations

import os
import tempfile

os.environ["REDIS_URL"] = ""
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pr3-tests-"))

import pytest  # noqa: E402

from dashboard.backend import entitlements as ent  # noqa: E402

ANON = None
USER = {"sub": 7, "email": "u@example.com", "role": "FREE"}
ADMIN = {"sub": 1, "email": "a@example.com", "role": "ADMIN"}
LEGACY_PREMIUM = {"sub": 9, "email": "p@example.com", "role": "PREMIUM"}


# ── the rule itself ──────────────────────────────────────────────────────────

def test_anonymous_is_not_entitled_signed_in_is():
    assert ent.is_entitled(ANON) is False
    assert ent.is_entitled(USER) is True


@pytest.mark.parametrize("user", [USER, ADMIN, LEGACY_PREMIUM])
def test_every_signed_in_role_gets_the_same_access(user):
    """No role is a plan. FREE must not be second-class while nothing is sold."""
    assert ent.is_entitled(user) is True


def test_preview_reason_is_truthful():
    assert ent.preview_reason(ANON) == "sign_in_required"
    assert ent.preview_reason(USER) is None


def test_entitlement_does_not_read_client_supplied_fields():
    """Identity comes from the verified JWT. A forged role must not matter —
    and today it cannot, because only presence is checked."""
    forged = {"sub": 3, "role": "PREMIUM", "is_admin": True, "entitled": True}
    assert ent.is_entitled(forged) == ent.is_entitled({"sub": 3, "role": "FREE"})


# ── research ideas: preview must not leak the hidden ideas ───────────────────

def _ideas(n: int) -> dict:
    return {"items": [{"symbol": f"SYM{i}", "entry": 100 + i, "stop": 90 + i,
                       "target": 120 + i} for i in range(n)], "count": n}


def test_anonymous_research_is_capped_and_flagged():
    from dashboard.backend.routes.research import ANON_PREVIEW_LIMIT, _gate_research_items

    out = _gate_research_items(_ideas(10), entitled=False)
    assert len(out["items"]) == ANON_PREVIEW_LIMIT
    assert out["gated"] is True
    assert out["gated_reason"] == "sign_in_required"
    assert out["total_available"] == 10      # count is honest about what exists
    assert out["count"] == ANON_PREVIEW_LIMIT


def test_anonymous_research_payload_does_not_contain_withheld_ideas():
    """THE leak test: the ideas beyond the cap must be absent from the
    serialised response, not merely hidden by the client."""
    import json

    from dashboard.backend.routes.research import ANON_PREVIEW_LIMIT, _gate_research_items

    out = _gate_research_items(_ideas(10), entitled=False)
    blob = json.dumps(out)
    for i in range(ANON_PREVIEW_LIMIT, 10):
        assert f"SYM{i}" not in blob, f"withheld idea SYM{i} leaked into the preview"
    # the withheld ideas' levels are gone with them
    withheld_targets = {120 + i for i in range(ANON_PREVIEW_LIMIT, 10)}
    shown_targets = {item["target"] for item in out["items"]}
    assert not (withheld_targets & shown_targets)


def test_signed_in_research_is_not_truncated():
    from dashboard.backend.routes.research import _gate_research_items

    full = _ideas(10)
    out = _gate_research_items(full, entitled=True)
    assert len(out["items"]) == 10
    assert "gated" not in out


def test_short_list_is_not_marked_gated_for_anyone():
    from dashboard.backend.routes.research import _gate_research_items

    out = _gate_research_items(_ideas(2), entitled=False)
    assert len(out["items"]) == 2
    assert "gated" not in out, "nothing was withheld, so nothing may claim to be gated"


# ── command centre feed ──────────────────────────────────────────────────────

def test_feed_limit_is_by_entitlement_not_role():
    from dashboard.backend.services import command_center_service as ccs

    assert ccs._feed_limit(False) == ccs.ANON_FEED_LIMIT
    assert ccs._feed_limit(True) == ccs.FULL_FEED_LIMIT
    assert ccs.FULL_FEED_LIMIT > ccs.ANON_FEED_LIMIT


def test_signed_in_user_gets_the_full_feed_not_the_anonymous_one():
    """The regression this PR fixes: a FREE account used to receive the
    anonymous feed length."""
    from dashboard.backend.services import command_center_service as ccs

    anon = ccs.build_command_center_payload(user_id=None, role=None, entitled=False)
    signed_in = ccs.build_command_center_payload(user_id=7, role="FREE", entitled=True)

    assert anon["premium"]["feed_limit"] == ccs.ANON_FEED_LIMIT
    assert signed_in["premium"]["feed_limit"] == ccs.FULL_FEED_LIMIT
    assert signed_in["premium"]["feed_limit"] != anon["premium"]["feed_limit"]


def test_payload_reports_access_truthfully_and_sells_nothing():
    from dashboard.backend.services import command_center_service as ccs

    anon = ccs.build_command_center_payload(user_id=None, role=None, entitled=False)
    signed_in = ccs.build_command_center_payload(user_id=7, role="FREE", entitled=True)

    assert anon["premium"]["authenticated"] is False
    assert anon["premium"]["preview_reason"] == "sign_in_required"
    assert anon["premium"]["tier"] == "PREVIEW"

    assert signed_in["premium"]["authenticated"] is True
    assert signed_in["premium"]["full_watchlist_intel"] is True
    assert signed_in["premium"]["preview_reason"] is None
    # no copy may promise a plan that cannot be bought
    assert "PREMIUM unlocks" not in signed_in["premium"]["note"]


def test_legacy_callers_without_the_entitled_flag_still_work():
    """Back-compat: a caller that passes only `role` must not regress."""
    from dashboard.backend.services import command_center_service as ccs

    legacy_anon = ccs.build_command_center_payload(user_id=None, role=None)
    legacy_auth = ccs.build_command_center_payload(user_id=7, role="FREE")
    assert legacy_anon["premium"]["feed_limit"] == ccs.ANON_FEED_LIMIT
    assert legacy_auth["premium"]["feed_limit"] == ccs.FULL_FEED_LIMIT


# ── extensibility: adding a plan later must not need another scatter ─────────

def test_feature_names_exist_for_a_future_plan():
    for feature in (ent.FEATURE_RESEARCH_IDEAS, ent.FEATURE_COMMAND_CENTER_FEED,
                    ent.FEATURE_WATCHLIST_INTEL, ent.FEATURE_EVOLUTION_TIMELINE):
        assert isinstance(feature, str) and feature
        # every feature resolves through the same question today
        assert ent.is_entitled(USER, feature) is True
        assert ent.is_entitled(ANON, feature) is False
