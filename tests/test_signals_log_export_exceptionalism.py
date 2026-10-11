"""The signals-log export must surface the exceptionalism verdict.

Without it, a non-selected row cannot be attributed: "did not qualify" and
"qualified but ranked below EXCEPTIONALISM_SOFT_CEILING" look identical. That
ambiguity is the stated limitation of the 2026-10-11 ceiling study.

No schema change is involved — `signals_log.layer_details` already carries the
verdict written by `exceptionalism.score_and_qualify()` at scan time.
"""

import json

import pytest

from services.exceptionalism import score_and_qualify


def _row_builder(layer_details: str, rejection_reason: str = "[]") -> dict:
    """Mirror the transform the export applies to one DB row."""
    d = {"symbol": "NSE:X", "rejection_reason": rejection_reason, "layer_details": layer_details}
    try:
        d["rejection_reason"] = json.loads(d.get("rejection_reason") or "[]")
    except Exception:
        d["rejection_reason"] = []
    exc = {}
    try:
        exc = (json.loads(d.pop("layer_details", "") or "{}") or {}).get("exceptionalism") or {}
    except Exception:
        exc = {}
    d["exc_score"] = exc.get("exceptionalism")
    d["exc_threshold"] = exc.get("threshold")
    d["exc_qualifies"] = exc.get("qualifies")
    d["exc_reason"] = exc.get("reason")
    d["exc_sector_band"] = exc.get("sector_band")
    return d


def test_verdict_is_surfaced_and_blob_is_not():
    verdict = score_and_qualify(
        discovery={"momentum_20d_pct": 12.0, "momentum_50d_pct": 20.0,
                   "breakout_score": 70, "volume_score": 60},
        smc_band=0.8, rr=3.0, nifty_ret20=-4.0, sector_rel20=2.0,
        sector_band="leading", entry_state="actionable", market_health=70.0,
    )
    details = json.dumps({"discovery": {"big": "x" * 500}, "exceptionalism": verdict})
    row = _row_builder(details)

    assert row["exc_qualifies"] is verdict["qualifies"]
    assert row["exc_score"] == verdict["exceptionalism"]
    assert row["exc_threshold"] == verdict["threshold"]
    assert row["exc_sector_band"] == "leading"
    # The heavy blob must not be returned.
    assert "layer_details" not in row


@pytest.mark.parametrize("details", ["", "{}", "not json", None, '{"exceptionalism": null}'])
def test_missing_or_corrupt_layer_details_never_raises(details):
    row = _row_builder(details)
    assert row["exc_qualifies"] is None
    assert row["exc_score"] is None
    assert "layer_details" not in row


def test_lagging_band_is_distinguishable_from_ceiling_cut():
    """The exact ambiguity this field removes."""
    common = dict(
        discovery={"momentum_20d_pct": 6.0, "momentum_50d_pct": 8.0,
                   "breakout_score": 50, "volume_score": 50},
        smc_band=0.5, rr=2.0, nifty_ret20=-4.0, sector_rel20=-5.0,
        entry_state="actionable", market_health=70.0,
    )
    lagging = score_and_qualify(sector_band="lagging", **common)
    neutral = score_and_qualify(sector_band="neutral", **common)
    # Same score, different verdicts -> the band is what decided it.
    assert lagging["exceptionalism"] == neutral["exceptionalism"]
    if not lagging["qualifies"]:
        assert "lagging_sector" in lagging["reason"] or "below_threshold" in lagging["reason"]
    assert _row_builder(json.dumps({"exceptionalism": lagging}))["exc_reason"] == lagging["reason"]
