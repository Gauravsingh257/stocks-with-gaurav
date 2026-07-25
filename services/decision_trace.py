"""
services/decision_trace.py — per-recommendation explainability (Phase 2 / EP3).

Turns the scores the engine already computes into a plain-language "why this
surfaced" the way an experienced portfolio manager would say it:

    "Most of the market is weak today (Health 34 · SCARCE), but ABC is showing
     extraordinary relative strength — exceptionalism 92 clears today's raised
     bar of 90, it's a leader in a leading sector (Realty), and it's at its
     entry zone."

Pure, dependency-light, and reused by the research route to attach a
`decision_trace` to every served card. Does NOT compute anything new — it reads
the exceptionalism verdict, market health, sector band, entry state and
confidence that already exist. Flag-free and additive: attaching a trace never
changes which stocks are shown.
"""

from __future__ import annotations


def _opportunity_from_health(health: float | None) -> str | None:
    if health is None:
        return None
    if health >= 70:
        return "RICH"
    if health >= 50:
        return "NORMAL"
    if health >= 30:
        return "SELECTIVE"
    return "SCARCE"


_SECTOR_PHRASE = {
    "leading": "a leader in a leading sector",
    "neutral": "in a neutral sector",
    "lagging": "in a lagging sector",
    "unknown": "in an unclassified sector",
}

_ENTRY_PHRASE = {
    "READY": "at its entry zone",
    "WATCH": "approaching its entry",
    "IN_MOTION": "already moving (entry passed)",
    "MISSED": "past a clean entry",
}


def selectivity_explainer(opportunity_level: str | None, shown: int, market_health: float | None = None) -> str:
    """The "why 2 today, not 12" line — teaches users that a short list is a
    feature of a weak tape, not a broken scanner."""
    lvl = (opportunity_level or _opportunity_from_health(market_health) or "").upper()
    h = f"Market Health {round(market_health)}/100" if isinstance(market_health, (int, float)) else "The market"
    if lvl in ("SCARCE", "SELECTIVE") or (isinstance(market_health, (int, float)) and market_health < 45):
        if shown == 0:
            return (f"{h} is weak — no stock is exceptional enough to clear today's raised bar. "
                    f"Holding cash is the call; we surface names only when the evidence is overwhelming.")
        noun = "name" if shown == 1 else "names"
        return (f"{h} is weak, so the bar is raised — most stocks don't qualify today. "
                f"These {shown} {noun} cleared it by showing strength that stands out against a soft market.")
    if lvl == "NORMAL":
        return f"{h} is mixed — a selective set of stronger setups qualifies today."
    return f"{h} is healthy — participation is broad, so more names qualify today."


def build_decision_trace(
    *,
    symbol: str | None = None,
    exceptionalism: dict | None = None,
    entry_state: str | None = None,
    confidence: float | None = None,
    setup: str | None = None,
    sector: str | None = None,
) -> dict:
    """Return {headline, trace, why_short, factors[]} for one recommendation.

    `exceptionalism` is the verdict dict from services.exceptionalism.score_and_qualify
    (score/threshold/qualifies/reason/sector_band/market_health). Everything is
    best-effort — missing inputs are simply omitted from the narrative.
    """
    exc = exceptionalism or {}
    score = exc.get("exceptionalism")
    threshold = exc.get("threshold")
    reason = exc.get("reason")
    band = (exc.get("sector_band") or "unknown").lower()
    health = exc.get("market_health")
    opp = _opportunity_from_health(health)
    sym = (symbol or "This stock").replace("NSE:", "")

    factors: list[dict] = []
    if score is not None and threshold is not None:
        clears = score >= threshold
        factors.append({
            "label": "Exceptionalism",
            "detail": f"{score:g} vs today's bar {threshold:g}",
            "tone": "positive" if clears else "negative",
        })
    if health is not None:
        factors.append({"label": "Market Health", "detail": f"{round(health)}/100" + (f" · {opp}" if opp else ""),
                        "tone": "positive" if health >= 50 else "caution"})
    if band != "unknown":
        factors.append({"label": "Sector", "detail": (sector or band).replace("_", " ").title() + f" ({band})",
                        "tone": "positive" if band == "leading" else "caution" if band == "lagging" else "neutral"})
    if entry_state:
        factors.append({"label": "Entry", "detail": entry_state.title().replace("_", " "),
                        "tone": "positive" if entry_state in ("READY", "WATCH") else "caution"})
    if confidence is not None:
        factors.append({"label": "Confidence", "detail": f"{round(float(confidence))}%", "tone": "neutral"})
    if setup:
        factors.append({"label": "Setup", "detail": str(setup).replace("_", " "), "tone": "neutral"})

    # Narrative.
    parts: list[str] = []
    if health is not None and health < 45:
        parts.append(f"Most of the market is weak today (Health {round(health)}"
                     + (f" · {opp}" if opp else "") + ")")
    elif health is not None:
        parts.append(f"Market Health is {round(health)}/100" + (f" ({opp})" if opp else ""))

    if score is not None and threshold is not None:
        if reason == "exceptional_override":
            parts.append(f"{sym} is an exceptional override — exceptionalism {score:g} clears the raised bar "
                         f"{threshold:g} strongly enough to qualify despite {_SECTOR_PHRASE.get(band, 'its sector')}")
        else:
            verb = "clears" if score >= threshold else "is short of"
            parts.append(f"{sym}'s exceptionalism {score:g} {verb} today's bar of {threshold:g}")
            if band in _SECTOR_PHRASE and band != "unknown":
                parts.append(_SECTOR_PHRASE[band])
    if entry_state in _ENTRY_PHRASE:
        parts.append(f"it's {_ENTRY_PHRASE[entry_state]}")

    trace = ". ".join(p for p in parts if p).strip()
    if trace and not trace.endswith("."):
        trace += "."

    if reason == "exceptional_override":
        headline = "Exceptional override"
    elif score is not None and threshold is not None and score >= threshold:
        headline = "Clears today's bar"
    else:
        headline = "Below today's bar"

    why_short = None
    if score is not None and threshold is not None:
        why_short = f"EXC {score:g} {'≥' if score >= threshold else '<'} {threshold:g}"
        if reason == "exceptional_override":
            why_short += " · override"

    return {"headline": headline, "trace": trace, "why_short": why_short, "factors": factors}
