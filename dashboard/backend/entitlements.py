"""
dashboard/backend/entitlements.py
=================================
One place that answers "may this caller see the full product?".

**There is no paid plan.** No billing system exists, every account in production
carries role FREE, and PREMIUM is unused. Until 2026-10-18 four call sites
nevertheless gated on `role in ("PREMIUM", "ADMIN")`, so a signed-in user was
served the same truncated payload as an anonymous visitor — a restriction that
protected nothing and advertised a tier that cannot be bought.

The honest rule while no plan exists:

    anonymous  -> a capped PREVIEW (aggregates and a sample; never the goods)
    signed in  -> the full product

Previews are built on the SERVER. Truncation happens before the response is
serialised, so a locked UI cannot leak what it is hiding — the hidden rows are
never sent. Never gate by hiding in the client.

Extensibility: when billing ships, give `is_entitled` a plan lookup (and add
feature names to `Feature`). Call sites ask the question, not the role, so they
will not need to change again.
"""

from __future__ import annotations

from typing import Any

# Named features, so a future plan can differ per feature without another
# scatter of role checks. All of them resolve the same way today.
FEATURE_RESEARCH_IDEAS = "research_ideas"
FEATURE_COMMAND_CENTER_FEED = "command_center_feed"
FEATURE_WATCHLIST_INTEL = "watchlist_intel"
FEATURE_EVOLUTION_TIMELINE = "evolution_timeline"


def is_authenticated(user: dict[str, Any] | None) -> bool:
    """A verified JWT was presented. `user` comes from get_optional_user /
    get_current_user, never from a client-supplied field."""
    return bool(user)


def is_entitled(user: dict[str, Any] | None, feature: str | None = None) -> bool:
    """May this caller see the full product?

    Today: being signed in is the entitlement. `feature` is accepted now so
    that call sites already pass it when plans arrive; it is deliberately not
    branched on yet, because inventing per-feature tiers before a plan exists
    would be fiction.
    """
    return is_authenticated(user)


def preview_reason(user: dict[str, Any] | None) -> str | None:
    """Why the caller is seeing a preview — for the UI to show a truthful CTA.
    None when they are entitled."""
    if is_entitled(user):
        return None
    return "sign_in_required"
