"""
Content Creation / agents / data_agent.py

DataAgent — production-grade data collection for stock market automation.

FREE APIs only:
  ┌────────────────────────────────────────────────────┐
  │  Source         │  Coverage     │  Weight           │
  ├────────────────────────────────────────────────────┤
  │  yfinance       │  Indian idx   │  50 % (India)     │
  │  NSE unofficial │  FII/DII, adv │  50 % (India)     │
  │  yfinance       │  Global idx   │  20 % (Global)    │
  │  CoinGecko      │  Top crypto   │  10 % (Crypto)    │
  │  Finnhub/News   │  Headlines    │  20 % (News)      │
  └────────────────────────────────────────────────────┘

Public functions:
    get_indian_market()  → dict   (indices, sectors, FII/DII, VIX, advance/decline)
    get_global_market()  → dict   (6 major global indices)
    get_crypto()         → dict   (top 5 coins)
    get_news()           → dict   (8-10 market headlines)
    get_data()           → dict   (combined JSON: india, global, crypto, news, sectors)

Every function is self-contained, handles failures gracefully,
and returns valid (possibly empty) data — never raises.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx

from agents.base import BaseContentAgent
from models.contracts import (
    FIIDIIData,
    GlobalMarket,
    IndexData,
    MarketData,
    NewsItem,
    PipelineMode,
    SectorData,
    Sentiment,
)

log = logging.getLogger("content_creation.agents.data")

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

_HTTP_TIMEOUT = 12  # seconds per request
_MAX_RETRIES = 2
_RETRY_BACKOFF = 1.5  # seconds

# ── yfinance symbols ──────────────────────────────────────────────────────

_INDIA_INDEX_SYMBOLS: dict[str, str] = {
    "NIFTY 50":   "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "SENSEX":     "^BSESN",
    "INDIA VIX":  "^INDIAVIX",
    "NIFTY IT":   "^CNXIT",
    "NIFTY MIDCAP 100": "^NSEMDCP50",
}

_INDIA_SECTOR_SYMBOLS: dict[str, str] = {
    "IT":       "^CNXIT",
    "Bank":     "^CNXBANK",
    "Pharma":   "^CNXPHARMA",
    "Auto":     "^CNXAUTO",
    "Metal":    "^CNXMETAL",
    "Energy":   "^CNXENERGY",
    "FMCG":     "^CNXFMCG",
    "Realty":   "^CNXREALTY",
}

_GLOBAL_SYMBOLS: dict[str, str] = {
    "S&P 500":    "^GSPC",
    "NASDAQ":     "^IXIC",
    "DOW JONES":  "^DJI",
    "HANG SENG":  "^HSI",
    "NIKKEI 225": "^N225",
    "FTSE 100":   "^FTSE",
}

# ── CoinGecko ─────────────────────────────────────────────────────────────

_COINGECKO_URL = "https://api.coingecko.com/api/v3"
_CRYPTO_IDS = ["bitcoin", "ethereum", "solana", "ripple", "dogecoin"]

# ── NSE unofficial endpoints ──────────────────────────────────────────────

_NSE_BASE = "https://www.nseindia.com"
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── News ──────────────────────────────────────────────────────────────────

_NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"
_GNEWS_URL = "https://gnews.io/api/v4/top-headlines"
_FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"

# ═══════════════════════════════════════════════════════════════════════════
#  HTTP HELPER — retry with backoff
# ═══════════════════════════════════════════════════════════════════════════


def _safe_get(
    client: httpx.Client,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = _MAX_RETRIES,
    label: str = "",
) -> dict | None:
    """
    GET with retry + backoff.  Returns parsed JSON or None.
    Never raises — all failures are logged and swallowed.
    """
    for attempt in range(1, retries + 1):
        try:
            resp = client.get(url, params=params, headers=headers)
            if resp.status_code == 429:
                # Rate limited — back off
                wait = _RETRY_BACKOFF * attempt
                log.warning("[%s] Rate limited (429) — waiting %.1fs", label, wait)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                log.warning("[%s] HTTP %d on attempt %d", label, resp.status_code, attempt)
                continue
            data = resp.json()
            if not data:
                log.warning("[%s] Empty JSON response", label)
                return None
            return data
        except httpx.TimeoutException:
            log.warning("[%s] Timeout on attempt %d/%d", label, attempt, retries)
        except Exception as e:
            log.warning("[%s] Error on attempt %d/%d: %s", label, attempt, retries, e)
        if attempt < retries:
            time.sleep(_RETRY_BACKOFF * attempt)
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  1.  INDIAN MARKET  (50 %)
#      yfinance (indices, sectors) + NSE unofficial (FII/DII, adv-dec)
# ═══════════════════════════════════════════════════════════════════════════


def _yf_quote(client: httpx.Client, symbol: str) -> dict | None:
    """Fetch a single quote via Yahoo Finance v8 chart endpoint."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    data = _safe_get(
        client, url,
        params={"range": "1d", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; StocksWithGaurav/1.0)"},
        label=f"yf:{symbol}",
    )
    if not data:
        return None
    try:
        meta = data["chart"]["result"][0]["meta"]
        curr = meta.get("regularMarketPrice", 0)
        prev = meta.get("chartPreviousClose", meta.get("previousClose", 0))
        return {"price": curr, "prev": prev}
    except (KeyError, IndexError, TypeError):
        return None


def _yf_bulk_quotes(
    client: httpx.Client, symbol_map: dict[str, str]
) -> list[dict[str, Any]]:
    """Fetch multiple symbols via yfinance and return list of dicts."""
    results: list[dict[str, Any]] = []
    for name, sym in symbol_map.items():
        q = _yf_quote(client, sym)
        if q and q["price"]:
            price = q["price"]
            prev = q["prev"] or price
            change = round(price - prev, 2)
            pct = round((change / prev) * 100, 2) if prev else 0.0
            trend = "up" if change > 0 else ("down" if change < 0 else "flat")
            results.append({
                "name":       name,
                "price":      round(price, 2),
                "change":     change,
                "change_pct": pct,
                "trend":      trend,
            })
    return results


def _fetch_nse_session(client: httpx.Client) -> httpx.Cookies | None:
    """Hit NSE homepage to get session cookies (required for API calls)."""
    try:
        resp = client.get(_NSE_BASE, headers=_NSE_HEADERS, follow_redirects=True)
        if resp.status_code == 200:
            return resp.cookies
    except Exception as e:
        log.warning("NSE session init failed: %s", e)
    return None


def _fetch_nse_fii_dii(client: httpx.Client, cookies: httpx.Cookies | None) -> dict:
    """Fetch FII/DII activity from NSE unofficial API."""
    if not cookies:
        return {"fii_net": 0, "dii_net": 0, "fii_trend": "", "dii_trend": ""}
    data = _safe_get(
        client,
        f"{_NSE_BASE}/api/fiidiiActivity",
        headers=_NSE_HEADERS,
        label="nse:fii_dii",
    )
    if not data:
        return {"fii_net": 0, "dii_net": 0, "fii_trend": "", "dii_trend": ""}
    try:
        fii_buy = fii_sell = dii_buy = dii_sell = 0.0
        for row in data:
            cat = row.get("category", "").upper()
            buy = float(row.get("buyValue", 0))
            sell = float(row.get("sellValue", 0))
            if "FII" in cat or "FPI" in cat:
                fii_buy += buy
                fii_sell += sell
            elif "DII" in cat:
                dii_buy += buy
                dii_sell += sell
        fii_net = round(fii_buy - fii_sell, 2)
        dii_net = round(dii_buy - dii_sell, 2)
        return {
            "fii_net":   fii_net,
            "dii_net":   dii_net,
            "fii_trend": "buying" if fii_net > 0 else "selling",
            "dii_trend": "buying" if dii_net > 0 else "selling",
        }
    except Exception as e:
        log.warning("FII/DII parse error: %s", e)
        return {"fii_net": 0, "dii_net": 0, "fii_trend": "", "dii_trend": ""}


def _fetch_nse_advance_decline(
    client: httpx.Client, cookies: httpx.Cookies | None
) -> str:
    """Fetch advance/decline ratio from NSE."""
    if not cookies:
        return ""
    data = _safe_get(
        client,
        f"{_NSE_BASE}/api/market-data-pre-open?key=NIFTY",
        headers=_NSE_HEADERS,
        label="nse:adv_dec",
    )
    if not data:
        return ""
    try:
        adv = data.get("advance", {}).get("advances", 0)
        dec = data.get("advance", {}).get("declines", 0)
        return f"{adv}:{dec}" if adv or dec else ""
    except Exception:
        return ""


def get_indian_market(
    client: httpx.Client | None = None,
    settings: Any = None,
) -> dict:
    """
    Fetch Indian market data.

    Returns:
        {
            "indices":         [{"name", "price", "change", "change_pct", "trend"}, ...],
            "sectors":         [{"name", "price", "change", "change_pct", "trend"}, ...],
            "fii_dii":         {"fii_net", "dii_net", "fii_trend", "dii_trend"},
            "vix":             float,
            "advance_decline": "adv:dec",
            "fetched_at":      ISO timestamp,
        }
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_HTTP_TIMEOUT)
    try:
        # NSE session for unofficial endpoints
        nse_cookies = _fetch_nse_session(client)

        indices = _yf_bulk_quotes(client, _INDIA_INDEX_SYMBOLS)
        sectors = _yf_bulk_quotes(client, _INDIA_SECTOR_SYMBOLS)
        fii_dii = _fetch_nse_fii_dii(client, nse_cookies)
        adv_dec = _fetch_nse_advance_decline(client, nse_cookies)

        vix = 0.0
        for idx in indices:
            if "VIX" in idx["name"].upper():
                vix = idx["price"]
                break

        result = {
            "indices":         indices,
            "sectors":         sectors,
            "fii_dii":         fii_dii,
            "vix":             vix,
            "advance_decline": adv_dec,
            "fetched_at":      datetime.now().isoformat(),
        }
        log.info("Indian market: %d indices, %d sectors, VIX=%.1f", len(indices), len(sectors), vix)
        return result
    finally:
        if own_client:
            client.close()


# ═══════════════════════════════════════════════════════════════════════════
#  2.  GLOBAL MARKET  (20 %)
#      yfinance for 6 major indices
# ═══════════════════════════════════════════════════════════════════════════


def get_global_market(client: httpx.Client | None = None) -> dict:
    """
    Fetch global market indices.

    Returns:
        {
            "markets": [{"name", "price", "change", "change_pct", "trend"}, ...],
            "fetched_at": ISO timestamp,
        }
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_HTTP_TIMEOUT)
    try:
        markets = _yf_bulk_quotes(client, _GLOBAL_SYMBOLS)
        log.info("Global market: %d indices fetched", len(markets))
        return {
            "markets":    markets,
            "fetched_at": datetime.now().isoformat(),
        }
    finally:
        if own_client:
            client.close()


# ═══════════════════════════════════════════════════════════════════════════
#  3.  CRYPTO  (10 %)
#      CoinGecko free API — no key required
# ═══════════════════════════════════════════════════════════════════════════


# ── CoinCap (fallback — free, no key, no strict rate limit) ───────────────

_COINCAP_URL = "https://api.coincap.io/v2/assets"
_COINCAP_IDS = ["bitcoin", "ethereum", "solana", "xrp", "dogecoin"]


def _fetch_coincap(client: httpx.Client) -> list[dict]:
    """Fetch top crypto from CoinCap API (free, no key required)."""
    coins: list[dict] = []
    for cid in _COINCAP_IDS:
        data = _safe_get(
            client,
            f"{_COINCAP_URL}/{cid}",
            label=f"coincap:{cid}",
        )
        if data and "data" in data:
            d = data["data"]
            price = float(d.get("priceUsd", 0) or 0)
            pct = float(d.get("changePercent24Hr", 0) or 0)
            coins.append({
                "name":           d.get("name", ""),
                "symbol":         d.get("symbol", "").upper(),
                "price_usd":      round(price, 2),
                "change_24h_pct": round(pct, 2),
                "market_cap":     int(float(d.get("marketCapUsd", 0) or 0)),
                "volume_24h":     int(float(d.get("volumeUsd24Hr", 0) or 0)),
                "trend":          "up" if pct > 0 else ("down" if pct < 0 else "flat"),
            })
    return coins


def get_crypto(client: httpx.Client | None = None) -> dict:
    """
    Fetch top cryptocurrencies from CoinGecko.

    Returns:
        {
            "coins": [
                {"name", "symbol", "price_usd", "change_24h_pct",
                 "market_cap", "volume_24h", "trend"},
                ...
            ],
            "fetched_at": ISO timestamp,
        }
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_HTTP_TIMEOUT)
    try:
        # Source 1: CoinGecko (preferred)
        data = _safe_get(
            client,
            f"{_COINGECKO_URL}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(_CRYPTO_IDS),
                "order": "market_cap_desc",
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
            label="coingecko",
        )
        coins: list[dict] = []
        if data and isinstance(data, list):
            for coin in data:
                pct = coin.get("price_change_percentage_24h", 0) or 0
                coins.append({
                    "name":           coin.get("name", ""),
                    "symbol":         coin.get("symbol", "").upper(),
                    "price_usd":      round(coin.get("current_price", 0), 2),
                    "change_24h_pct": round(pct, 2),
                    "market_cap":     coin.get("market_cap", 0),
                    "volume_24h":     coin.get("total_volume", 0),
                    "trend":          "up" if pct > 0 else ("down" if pct < 0 else "flat"),
                })

        # Source 2: CoinCap fallback (free, no key, no rate limit)
        if not coins:
            log.info("CoinGecko unavailable, trying CoinCap API")
            coins = _fetch_coincap(client)

        log.info("Crypto: %d coins fetched", len(coins))
        return {"coins": coins, "fetched_at": datetime.now().isoformat()}
    finally:
        if own_client:
            client.close()


# ═══════════════════════════════════════════════════════════════════════════
#  4.  NEWS  (20 %)
#      Finnhub (free tier: 60 calls/min) → NewsAPI → GNews → fallback
# ═══════════════════════════════════════════════════════════════════════════


def _fetch_finnhub_news(client: httpx.Client, api_key: str) -> list[dict]:
    """Fetch general market news from Finnhub."""
    data = _safe_get(
        client,
        _FINNHUB_NEWS_URL,
        params={"category": "general", "token": api_key},
        label="finnhub:news",
    )
    if not data or not isinstance(data, list):
        return []
    items: list[dict] = []
    for article in data[:8]:
        items.append({
            "headline": article.get("headline", ""),
            "source":   article.get("source", ""),
            "url":      article.get("url", ""),
            "category": article.get("category", "general"),
            "time":     article.get("datetime", 0),
        })
    return items


def _fetch_newsapi(client: httpx.Client, api_key: str) -> list[dict]:
    """Fetch Indian business headlines from NewsAPI with images."""
    data = _safe_get(
        client,
        _NEWSAPI_URL,
        params={
            "country": "in",
            "category": "business",
            "pageSize": 8,
            "apiKey": api_key,
        },
        label="newsapi",
    )
    if not data:
        return []
    items: list[dict] = []
    for article in data.get("articles", [])[:8]:
        items.append({
            "headline": article.get("title", ""),
            "source":   article.get("source", {}).get("name", ""),
            "url":      article.get("url", ""),
            "image_url": article.get("urlToImage", ""),
            "category": "business",
        })
    return items


def _fetch_google_news_rss(client: httpx.Client) -> list[dict]:
    """Fetch Indian business news from Google News RSS (free, no key needed)."""
    import xml.etree.ElementTree as ET

    rss_url = "https://news.google.com/rss/search?q=indian+stock+market+OR+nifty+OR+sensex&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        resp = client.get(
            rss_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StocksWithGaurav/1.0)"},
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            log.warning("[google_news_rss] HTTP %d", resp.status_code)
            return []
        root = ET.fromstring(resp.text)
        items: list[dict] = []
        for item in root.findall(".//item")[:8]:
            title = item.findtext("title", "")
            source = item.findtext("source", "")
            link = item.findtext("link", "")
            if title:
                # Google News ALWAYS appends " - SourceName" to titles
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    if not source:
                        source = parts[1].strip()
                # Clean any remaining trailing dash/date fragments
                title = title.rstrip(" -:,")
                items.append({
                    "headline": title[:180],
                    "source":   source[:30],
                    "url":      link,
                    "image_url": "",
                    "category": "business",
                })
        log.info("Google News RSS: %d headlines fetched", len(items))
        return items
    except Exception as e:
        log.warning("[google_news_rss] Error: %s", e)
        return []


def _fetch_gnews(client: httpx.Client, api_key: str) -> list[dict]:
    """Fetch business news from GNews with image URLs."""
    data = _safe_get(
        client,
        _GNEWS_URL,
        params={
            "country": "in",
            "category": "business",
            "max": 8,
            "apikey": api_key,
        },
        label="gnews",
    )
    if not data:
        return []
    items: list[dict] = []
    for article in data.get("articles", [])[:8]:
        items.append({
            "headline": article.get("title", ""),
            "source":   article.get("source", {}).get("name", ""),
            "url":      article.get("url", ""),
            "image_url": article.get("image", ""),
            "category": "business",
        })
    return items


def get_news(
    client: httpx.Client | None = None,
    settings: Any = None,
) -> dict:
    """
    Fetch market news from 3 sources (waterfall: Finnhub → NewsAPI → GNews).

    Returns:
        {
            "headlines": [{"headline", "source", "url", "category"}, ...],
            "source_used": str,
            "fetched_at": ISO timestamp,
        }
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=_HTTP_TIMEOUT)

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
    newsapi_key = getattr(settings, "newsapi_key", "") if settings else os.environ.get("NEWS_API_KEY", "")
    gnews_key = getattr(settings, "gnews_api_key", "") if settings else os.environ.get("GNEWS_API_KEY", "")

    try:
        # Source 1: Finnhub (preferred — generous free tier, real-time)
        if finnhub_key:
            items = _fetch_finnhub_news(client, finnhub_key)
            if items:
                log.info("News: %d headlines from Finnhub", len(items))
                return {"headlines": items, "source_used": "finnhub", "fetched_at": datetime.now().isoformat()}

        # Source 2: NewsAPI
        if newsapi_key:
            items = _fetch_newsapi(client, newsapi_key)
            if items:
                log.info("News: %d headlines from NewsAPI", len(items))
                return {"headlines": items, "source_used": "newsapi", "fetched_at": datetime.now().isoformat()}

        # Source 3: GNews
        if gnews_key:
            items = _fetch_gnews(client, gnews_key)
            if items:
                log.info("News: %d headlines from GNews", len(items))
                return {"headlines": items, "source_used": "gnews", "fetched_at": datetime.now().isoformat()}

        # Source 4: Google News RSS (free, no key needed)
        items = _fetch_google_news_rss(client)
        if items:
            return {"headlines": items, "source_used": "google_rss", "fetched_at": datetime.now().isoformat()}

        # Fallback: static placeholders
        log.info("News: all sources failed — using fallback headlines")
        return {
            "headlines": [
                {"headline": "Market awaiting key economic data release this week", "source": "System", "url": "", "category": "business"},
                {"headline": "FII and DII flows continue to drive market direction", "source": "System", "url": "", "category": "business"},
                {"headline": "IT sector shows mixed signals ahead of earnings season", "source": "System", "url": "", "category": "business"},
                {"headline": "RBI monetary policy decision expected to impact markets", "source": "System", "url": "", "category": "business"},
                {"headline": "Global trade tensions add uncertainty to emerging markets", "source": "System", "url": "", "category": "business"},
            ],
            "source_used": "fallback",
            "fetched_at": datetime.now().isoformat(),
        }
    finally:
        if own_client:
            client.close()


# ═══════════════════════════════════════════════════════════════════════════
#  5.  COMBINED   get_data()
#      Merges all four sources into a single JSON structure
# ═══════════════════════════════════════════════════════════════════════════


def get_data(settings: Any = None) -> dict:
    """
    Master collection function — fetches everything and returns:

    {
        "india":   { indices, sectors, fii_dii, vix, advance_decline },
        "global":  { markets },
        "crypto":  { coins },
        "news":    { headlines },
        "sectors": [ {name, change_pct, trend} ],
        "meta": {
            "fetched_at": ISO timestamp,
            "sources":    ["yfinance", "nse", "coingecko", "finnhub/newsapi"],
        }
    }
    """
    t0 = time.perf_counter()

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        india = get_indian_market(client=client, settings=settings)
        globe = get_global_market(client=client)
        crypto = get_crypto(client=client)
        news = get_news(client=client, settings=settings)

    # Build flat sectors list for convenience
    sectors_flat = []
    for s in india.get("sectors", []):
        sectors_flat.append({
            "name":       s["name"],
            "change_pct": s["change_pct"],
            "trend":      s["trend"],
        })

    elapsed = round(time.perf_counter() - t0, 2)
    sources = ["yfinance"]
    if india.get("fii_dii", {}).get("fii_net"):
        sources.append("nse")
    if crypto.get("coins"):
        sources.append("coingecko")
    sources.append(news.get("source_used", "fallback"))

    result = {
        "india":   india,
        "global":  globe,
        "crypto":  crypto,
        "news":    news,
        "sectors": sectors_flat,
        "meta": {
            "fetched_at":     datetime.now().isoformat(),
            "duration_secs":  elapsed,
            "sources":        sources,
        },
    }
    log.info(
        "get_data() complete in %.1fs — India:%d  Global:%d  Crypto:%d  News:%d",
        elapsed,
        len(india.get("indices", [])),
        len(globe.get("markets", [])),
        len(crypto.get("coins", [])),
        len(news.get("headlines", [])),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT CLASS — integrates with the pipeline orchestrator
# ═══════════════════════════════════════════════════════════════════════════


class DataAgent(BaseContentAgent):
    name = "DataAgent"
    description = "Fetches Indian, global, crypto, and news data from free APIs"

    def run(self, *, mode: PipelineMode = PipelineMode.PRE_MARKET) -> MarketData:
        """
        Pipeline-compatible run method.
        Calls get_data() internally and maps to the MarketData contract.
        """
        raw = get_data(settings=self.settings)

        # ── Map raw → MarketData contract ─────────────────────────────────
        indices = [
            IndexData(
                name=i["name"],
                value=i["price"],
                change=i["change"],
                change_pct=i["change_pct"],
                trend=i["trend"],
            )
            for i in raw["india"].get("indices", [])
        ]

        global_markets = [
            GlobalMarket(
                name=m["name"],
                value=m["price"],
                change_pct=m["change_pct"],
            )
            for m in raw["global"].get("markets", [])
        ]

        news_items = [
            NewsItem(
                headline=n["headline"],
                source=n.get("source", ""),
                url=n.get("url", ""),
                image_url=n.get("image_url", ""),
            )
            for n in raw["news"].get("headlines", [])
        ]

        sectors = [
            SectorData(
                name=s["name"],
                change_pct=s["change_pct"],
            )
            for s in raw["india"].get("sectors", [])
        ]

        fii_raw = raw["india"].get("fii_dii", {})
        fii_dii = FIIDIIData(
            fii_net=fii_raw.get("fii_net", 0),
            dii_net=fii_raw.get("dii_net", 0),
            fii_trend=fii_raw.get("fii_trend", ""),
            dii_trend=fii_raw.get("dii_trend", ""),
        )

        # Store crypto + meta in raw_extras for downstream agents
        market_data = MarketData(
            mode=mode,
            timestamp=datetime.now(),
            indices=indices,
            global_markets=global_markets,
            news=news_items,
            sectors=sectors,
            fii_dii=fii_dii,
            vix=raw["india"].get("vix", 0),
            advance_decline=raw["india"].get("advance_decline", ""),
            raw_extras={
                "crypto": raw.get("crypto", {}),
                "meta":   raw.get("meta", {}),
            },
        )

        log.info(
            "DataAgent: %d indices, %d global, %d news, %d sectors, VIX=%.1f",
            len(indices), len(global_markets), len(news_items), len(sectors), market_data.vix,
        )
        return market_data


# ═══════════════════════════════════════════════════════════════════════════
#  STANDALONE CLI
#      python -m agents.data_agent          → pretty-print all data
#      python -m agents.data_agent --json   → raw JSON output
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    raw_json = "--json" in sys.argv
    data = get_data()

    if raw_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print("\n" + "=" * 60)
        print("  MARKET DATA COLLECTION")
        print("=" * 60)

        print(f"\n📈 INDIA ({len(data['india'].get('indices', []))} indices)")
        for idx in data["india"].get("indices", []):
            arrow = "▲" if idx["trend"] == "up" else "▼" if idx["trend"] == "down" else "─"
            print(f"   {idx['name']:20s}  {idx['price']:>10,.2f}  {arrow} {idx['change_pct']:+.2f}%")

        print(f"\n🌍 GLOBAL ({len(data['global'].get('markets', []))} markets)")
        for m in data["global"].get("markets", []):
            arrow = "▲" if m["trend"] == "up" else "▼" if m["trend"] == "down" else "─"
            print(f"   {m['name']:20s}  {m['price']:>10,.2f}  {arrow} {m['change_pct']:+.2f}%")

        print(f"\n₿  CRYPTO ({len(data['crypto'].get('coins', []))} coins)")
        for c in data["crypto"].get("coins", []):
            arrow = "▲" if c["trend"] == "up" else "▼" if c["trend"] == "down" else "─"
            print(f"   {c['name']:20s}  ${c['price_usd']:>10,.2f}  {arrow} {c['change_24h_pct']:+.2f}%")

        print(f"\n📰 NEWS ({len(data['news'].get('headlines', []))} headlines)")
        for n in data["news"].get("headlines", []):
            print(f"   • {n['headline'][:80]}")

        print(f"\n🏭 SECTORS ({len(data['sectors'])} sectors)")
        for s in data["sectors"]:
            arrow = "▲" if s["trend"] == "up" else "▼" if s["trend"] == "down" else "─"
            print(f"   {s['name']:20s}  {arrow} {s['change_pct']:+.2f}%")

        meta = data["meta"]
        print(f"\n⏱  Duration: {meta['duration_secs']}s | Sources: {', '.join(meta['sources'])}")
        print("=" * 60)
