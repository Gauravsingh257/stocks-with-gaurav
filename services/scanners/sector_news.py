"""
services/scanners/sector_news.py — Real-time sector news + tone via GDELT DOC 2.0.

The catalyst layer for the Sector Rotation scanner. Answers: "is this sector in
the news right now, and is the coverage positive or negative?" — using a FREE,
keyless, authentic, real-time source.

Source: GDELT DOC 2.0 API (https://api.gdeltproject.org/api/v2/doc/doc)
  - No API key, no account, no subscription.
  - Indexes global broadcast/print/web news, refreshed every 15 minutes.
  - We filter to `sourcecountry:india` + per-sector keywords.
  - `mode=artlist`   → recent articles (news HEAT = article count + top headline)
  - `mode=timelinetone` → average TONE (sentiment: >0 positive, <0 negative)

Honesty rules (matches services/sector_strength.py):
  - This is a BEST-EFFORT enrichment. If GDELT is unreachable or returns nothing,
    a sector simply has `news_heat=0, tone=None` — we never fabricate a number.
  - The Sector Rotation scan still works on real sector-index momentum alone;
    news only tilts the score, it is not a hard gate.

Cached in Redis (default 45 min) so the intraday scanner refreshes reuse one
fetch instead of hammering GDELT.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

log = logging.getLogger("services.scanners.sector_news")

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT throttles free traffic to ~1 request / 5 seconds (HTTP 429 otherwise).
# A single global spacing guard keeps the whole producer under the limit. The
# scanner cron is a background worker, so paying a few seconds per leading sector
# is fine; we only fetch news for the handful of sectors that are actually leading.
_MIN_INTERVAL = float(os.getenv("SECTOR_NEWS_MIN_INTERVAL", "6.5"))
_MAX_RETRIES = int(os.getenv("SECTOR_NEWS_MAX_RETRIES", "3"))
_last_call = [0.0]
_call_lock = threading.Lock()


def _throttle() -> None:
    with _call_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()

# Per-sector search term (India-scoped). GDELT DOC 2.0 is reliable with a single
# strong keyword or a short quoted phrase; heavy parenthesised OR groups tend to
# return nothing. We keep ONE representative term per sector — precise enough to
# gauge that sector's news heat + tone, robust against the query parser.
# Multi-word terms are quoted so GDELT treats them as a phrase.
SECTOR_QUERY_TERMS: dict[str, str] = {
    "Banking": "bank",
    "Finance": "finance",
    "Insurance": "insurance",
    "IT": "software",
    "Pharma": "pharmaceutical",
    "Auto": "automobile",
    "Metal": "steel",
    "Energy": "energy",
    "FMCG": '"consumer goods"',
    "Realty": '"real estate"',
    "Infra": "infrastructure",
    "Cement": "cement",
    "Chemicals": "chemicals",
    "Telecom": "telecom",
}

_REDIS_KEY_PREFIX = "sector:news:"
_REDIS_TTL = int(os.getenv("SECTOR_NEWS_TTL_SEC", "2700"))       # 45 min
_HEAT_TIMESPAN = os.getenv("SECTOR_NEWS_HEAT_TIMESPAN", "3days")  # recency window
_TONE_TIMESPAN = os.getenv("SECTOR_NEWS_TONE_TIMESPAN", "1week")
_MAX_ARTICLES = int(os.getenv("SECTOR_NEWS_MAX_ARTICLES", "50"))
_HTTP_TIMEOUT = float(os.getenv("SECTOR_NEWS_HTTP_TIMEOUT", "8"))
_ENABLED = os.getenv("SECTOR_NEWS_ENABLED", "1").strip().lower() in ("1", "true", "yes")

# News-heat normalisation: article count that counts as "hot" coverage.
_HEAT_SATURATION = float(os.getenv("SECTOR_NEWS_HEAT_SAT", "25"))


@dataclass(slots=True)
class SectorNews:
    sector: str
    article_count: int
    news_heat: float          # 0..1 normalised coverage intensity
    tone: float | None        # avg GDELT tone (>0 positive, <0 negative), or None
    top_headline: str | None
    top_url: str | None
    top_domain: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "article_count": self.article_count,
            "news_heat": round(self.news_heat, 3),
            "tone": None if self.tone is None else round(self.tone, 2),
            "top_headline": self.top_headline,
            "top_url": self.top_url,
            "top_domain": self.top_domain,
        }


def _redis_key() -> str:
    return f"{_REDIS_KEY_PREFIX}{date.today().isoformat()}"


def _gdelt_get(params: dict[str, str]) -> dict | None:
    """Single throttled GDELT DOC 2.0 GET returning parsed JSON, or None.

    Respects the ~1 req/5s free limit; retries once on a 429 rate-limit reply.
    A JSON-looking body starting with '{' is required (GDELT returns a plaintext
    rate-limit notice with a 200/429 that must not be parsed as data).
    """
    try:
        import requests
    except Exception:
        return None

    base = {"format": "json"}
    base.update(params)
    headers = {"User-Agent": "smc-sector-rotation/1.0 (+stockswithgaurav.com)"}

    for attempt in range(_MAX_RETRIES):
        _throttle()
        try:
            resp = requests.get(GDELT_DOC_URL, params=base, timeout=_HTTP_TIMEOUT, headers=headers)
        except Exception as exc:
            log.debug("GDELT fetch failed (%s): %s", params.get("mode"), exc)
            return None
        body = resp.text.strip()
        if resp.status_code == 429 or body.lower().startswith("please limit"):
            # Rate-limited: back off progressively and retry.
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_MIN_INTERVAL * (attempt + 1))
                continue
            log.debug("GDELT rate-limited after %d tries (%s)", _MAX_RETRIES, params.get("mode"))
            return None
        if resp.status_code != 200 or not body.startswith("{"):
            log.debug("GDELT %s -> HTTP %s (non-json)", params.get("mode"), resp.status_code)
            return None
        try:
            return resp.json()
        except Exception:
            return None
    return None


def _fetch_heat(query: str) -> tuple[int, str | None, str | None, str | None]:
    """artlist → (article_count, top_headline, top_url, top_domain)."""
    data = _gdelt_get({
        "query": query,
        "mode": "artlist",
        "timespan": _HEAT_TIMESPAN,
        "maxrecords": str(_MAX_ARTICLES),
        "sort": "datedesc",
    })
    arts = (data or {}).get("articles") or []
    if not arts:
        return 0, None, None, None
    top = arts[0]
    return (
        len(arts),
        (top.get("title") or "").strip() or None,
        top.get("url"),
        top.get("domain"),
    )


def _fetch_tone(query: str) -> float | None:
    """timelinetone → mean tone across the window, or None."""
    data = _gdelt_get({
        "query": query,
        "mode": "timelinetone",
        "timespan": _TONE_TIMESPAN,
    })
    try:
        series = (data or {}).get("timeline") or []
        points = series[0].get("data") if series else []
        vals = [float(p["value"]) for p in points if p.get("value") is not None]
        return sum(vals) / len(vals) if vals else None
    except Exception:
        return None


def _compute_one(sector: str) -> SectorNews:
    terms = SECTOR_QUERY_TERMS.get(sector)
    if not terms:
        return SectorNews(sector, 0, 0.0, None, None, None, None)
    # `sourcecountry:IN` (FIPS 2-letter) is the reliable India filter for DOC 2.0.
    query = f"sourcecountry:IN {terms}"
    count, headline, url, domain = _fetch_heat(query)
    tone = _fetch_tone(query) if count > 0 else None
    heat = min(count / _HEAT_SATURATION, 1.0) if _HEAT_SATURATION > 0 else 0.0
    return SectorNews(sector, count, heat, tone, headline, url, domain)


def get_sector_news(sectors: list[str], *, refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Return {sector: news_dict} for the given sectors. Redis-cached 45 min.

    Best-effort: on any failure a sector maps to zero heat / None tone — never
    fabricated. Returns {} entirely if the news layer is disabled.
    """
    if not _ENABLED:
        return {}

    key = _redis_key()
    cached: dict[str, Any] = {}
    if not refresh:
        try:
            from dashboard.backend.cache import get as cache_get

            blob = cache_get(key)
            if isinstance(blob, dict) and isinstance(blob.get("sectors"), dict):
                cached = blob["sectors"]
        except Exception:
            cached = {}

    out: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for sec in sectors:
        if sec in cached:
            out[sec] = cached[sec]
        else:
            missing.append(sec)

    for sec in missing:
        try:
            out[sec] = _compute_one(sec).to_dict()
        except Exception as exc:
            log.debug("sector news compute failed for %s: %s", sec, exc)
            out[sec] = SectorNews(sec, 0, 0.0, None, None, None, None).to_dict()

    # Merge + persist (so partial fetches accumulate within the TTL window).
    if missing:
        merged = dict(cached)
        merged.update(out)
        try:
            from dashboard.backend.cache import set as cache_set

            cache_set(key, {"sectors": merged, "_built_at": time.time()}, ttl_seconds=_REDIS_TTL)
        except Exception as exc:
            log.debug("sector news cache write failed: %s", exc)

    return out
