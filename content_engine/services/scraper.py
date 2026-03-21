"""
content_engine/services/scraper.py

Fetches market news and price data from external APIs.

Supported sources (in priority order):
  1. NewsAPI  — https://newsapi.org
  2. GNews    — https://gnews.io
  3. Fallback — returns empty list gracefully

All functions are async-safe and include retry logic.
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from content_engine.config.settings import ContentEngineSettings

log = logging.getLogger("content_engine.scraper")


# ── helpers ───────────────────────────────────────────────────────────────────

def _retry(max_attempts: int, backoff: int):
    """Simple synchronous retry decorator for HTTP calls."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    log.warning(
                        "Attempt %d/%d failed for %s: %s",
                        attempt, max_attempts, fn.__name__, exc,
                    )
                    if attempt < max_attempts:
                        time.sleep(backoff * attempt)
            raise RuntimeError(
                f"{fn.__name__} failed after {max_attempts} attempts"
            ) from last_exc
        return wrapper
    return decorator


# ── NewsAPI ───────────────────────────────────────────────────────────────────

def _fetch_newsapi(
    query: str, api_key: str, timeout: int, max_results: int = 10
) -> list[dict[str, Any]]:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max_results,
        "apiKey": api_key,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
    articles = resp.json().get("articles", [])
    return [
        {
            "title": a.get("title", ""),
            "description": a.get("description", ""),
            "url": a.get("url", ""),
            "source": a.get("source", {}).get("name", "NewsAPI"),
            "published_at": a.get("publishedAt", ""),
        }
        for a in articles
    ]


# ── GNews ─────────────────────────────────────────────────────────────────────

def _fetch_gnews(
    query: str, api_key: str, timeout: int, max_results: int = 10
) -> list[dict[str, Any]]:
    url = "https://gnews.io/api/v4/search"
    params = {
        "q": query,
        "lang": "en",
        "max": max_results,
        "token": api_key,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
    articles = resp.json().get("articles", [])
    return [
        {
            "title": a.get("title", ""),
            "description": a.get("description", ""),
            "url": a.get("url", ""),
            "source": a.get("source", {}).get("name", "GNews"),
            "published_at": a.get("publishedAt", ""),
        }
        for a in articles
    ]


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_market_news(
    settings: ContentEngineSettings,
    query: str = "Indian stock market NIFTY BANKNIFTY",
    max_results: int = 8,
) -> list[dict[str, Any]]:
    """
    Fetch news articles for the given query.
    Tries NewsAPI first, then GNews, then returns empty list on failure.
    """
    timeout = settings.http_timeout_seconds
    retried_newsapi = _retry(settings.max_retries, settings.retry_backoff_seconds)(
        _fetch_newsapi
    )
    retried_gnews = _retry(settings.max_retries, settings.retry_backoff_seconds)(
        _fetch_gnews
    )

    if settings.newsapi_key:
        try:
            articles = retried_newsapi(query, settings.newsapi_key, timeout, max_results)
            log.info("Fetched %d articles from NewsAPI", len(articles))
            return articles
        except Exception as exc:
            log.warning("NewsAPI failed: %s — trying GNews", exc)

    if settings.gnews_api_key:
        try:
            articles = retried_gnews(query, settings.gnews_api_key, timeout, max_results)
            log.info("Fetched %d articles from GNews", len(articles))
            return articles
        except Exception as exc:
            log.warning("GNews failed: %s — returning empty list", exc)

    log.error("All news sources failed or no API keys configured")
    return []


def fetch_index_prices(
    settings: ContentEngineSettings,
    symbols: list[str] | None = None,
) -> dict[str, float]:
    """
    Fetch current index prices from Yahoo Finance (no API key needed).
    Returns {symbol: price} dict. Returns empty dict on failure.

    Yahoo Finance symbol map:
      NIFTY 50 → ^NSEI, BANKNIFTY → ^NSEBANK, SENSEX → ^BSESN
    """
    symbol_map = {
        "NIFTY 50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "SENSEX": "^BSESN",
    }
    targets = symbols or settings.default_symbols
    result: dict[str, float] = {}

    for name in targets:
        ticker = symbol_map.get(name, name)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": "1d"}
        try:
            with httpx.Client(timeout=settings.http_timeout_seconds) as client:
                resp = client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
            data = resp.json()
            price = (
                data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            )
            result[name] = float(price)
        except Exception as exc:
            log.warning("Could not fetch price for %s: %s", name, exc)

    return result
