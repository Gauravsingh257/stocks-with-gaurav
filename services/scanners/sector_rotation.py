"""
services/scanners/sector_rotation.py — "Leading stock in a leading sector".

Scanner #2. Unlike the per-symbol Supertrend scan, this one is CROSS-SECTIONAL:
it first decides which sectors are rotating in, then only keeps strong stocks
that belong to those sectors.

Three real, honest layers (no synthetic data):
  1. SECTOR momentum  — computed here from the CONSTITUENT stocks' own real Kite
     OHLC (already fetched for this scan): a sector's relative strength = its
     stocks' mean return minus the whole scanned universe's mean return. This is
     self-contained (no yfinance index calls, which are blocked from Railway's
     datacenter IP) and reliable. A sector "leads" when it clearly outperforms
     the market breadth.
  2. NEWS catalyst    — services.scanners.sector_news: GDELT DOC 2.0 per-sector
     news heat + tone (free, keyless, real-time). Best-effort; tilts score only.
  3. STOCK strength   — the stock itself must be in a confirmed uptrend (EMA
     stack) with positive 1-month momentum and be liquid.

`prepare(data)` runs the cross-sectional layers ONCE per scan (given the whole
universe's candles) and returns a context dict. `evaluate(candles, bars_per_year,
symbol, ctx)` is then called per symbol by the runner, gated against that context.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any

from services.scanners.indicators import ema, atr_wilder

log = logging.getLogger("services.scanners.sector_rotation")

# Stock-layer gates (env-tunable).
_REQUIRE_STACK = os.getenv("SECTOR_ROT_REQUIRE_STACK", "1").strip().lower() in ("1", "true", "yes")
_MIN_MOM_LONG = float(os.getenv("SECTOR_ROT_MIN_MOM", "0.0"))   # 1-month momentum floor (%)
_MIN_BARS = int(os.getenv("SECTOR_ROT_MIN_BARS", "60"))

# Sector-layer config.
_LEAD_REL = float(os.getenv("SECTOR_ROT_LEAD_REL", "2.0"))          # sector must beat market breadth by ≥ this (%) on the medium window
_MIN_CONSTITUENTS = int(os.getenv("SECTOR_ROT_MIN_CONSTITUENTS", "2"))  # need ≥N stocks with data to trust a sector's reading
_REL_SHORT_BARS = int(os.getenv("SECTOR_ROT_REL_SHORT_BARS", "20"))  # medium-term window (≈1mo daily / 20wk weekly)
_REL_LONG_BARS = int(os.getenv("SECTOR_ROT_REL_LONG_BARS", "50"))    # longer window; leaders should also be ≥ market here


def _pct_return(closes: list[float], n: int) -> float | None:
    """N-bar % return; falls back to the longest available window if series short."""
    if len(closes) < 3:
        return None
    if len(closes) <= n:
        n = len(closes) - 1
    a, b = closes[-(n + 1)], closes[-1]
    if a <= 0:
        return None
    return (b - a) / a * 100.0


def _mean(vals: list[float]) -> float | None:
    return (sum(vals) / len(vals)) if vals else None


def prepare(data: dict[str, list[dict]]) -> dict[str, Any]:
    """Build the shared cross-sectional context for one scan run.

    `data` is {symbol: candles} — the whole universe's OHLC already fetched by the
    runner. Sector strength is derived from those constituents (no external calls):
    each sector's relative strength = its stocks' mean return minus the scanned
    universe's mean return, over a medium and a longer window. A sector "leads"
    when it beats market breadth by ≥ _LEAD_REL on the medium window and is not
    below breadth on the longer one.

    Returns:
      {
        "leading_sectors": {sector: {rel20, rel50, band, constituents, news:{...}}},
        "sym_sector":      {symbol: sector},
        "sector_diag":     {sector: rel20}   # all sectors, for observability
      }
    Degrades gracefully: on any failure returns an empty leading set (→ scan
    yields zero) rather than crashing the run.
    """
    try:
        from engine.swing import get_sector
    except Exception as exc:
        log.warning("sector_rotation.prepare: get_sector import failed (%s)", exc)
        return {"leading_sectors": {}, "sym_sector": {}}

    sym_sector: dict[str, str] = {}
    ret_short: dict[str, float] = {}
    ret_long: dict[str, float] = {}
    for sym, candles in (data or {}).items():
        sym_sector[sym] = get_sector(sym)
        if not candles or len(candles) < 25:
            continue
        try:
            closes = [float(c["close"]) for c in candles]
        except Exception:
            continue
        rs = _pct_return(closes, _REL_SHORT_BARS)
        rl = _pct_return(closes, _REL_LONG_BARS)
        if rs is not None:
            ret_short[sym] = rs
        if rl is not None:
            ret_long[sym] = rl

    # Market breadth = mean return across every stock that had data.
    uni_short = _mean(list(ret_short.values()))
    uni_long = _mean(list(ret_long.values()))
    if uni_short is None:
        log.warning("sector_rotation.prepare: no usable returns in universe; 0 leading")
        return {"leading_sectors": {}, "sym_sector": sym_sector}

    by_sector: dict[str, list[str]] = defaultdict(list)
    for sym, sec in sym_sector.items():
        by_sector[sec].append(sym)

    leading: dict[str, dict[str, Any]] = {}
    diag: dict[str, float] = {}
    for sec, syms in by_sector.items():
        if not sec or sec in ("Others", "Unknown"):
            continue
        s_short = [ret_short[s] for s in syms if s in ret_short]
        s_long = [ret_long[s] for s in syms if s in ret_long]
        if len(s_short) < _MIN_CONSTITUENTS:
            continue
        sec_short = _mean(s_short)
        sec_long = _mean(s_long)
        rel20 = sec_short - uni_short
        rel50 = (sec_long - uni_long) if (sec_long is not None and uni_long is not None) else None
        diag[sec] = round(rel20, 2)
        if rel20 >= _LEAD_REL and (rel50 is None or rel50 >= 0):
            leading[sec] = {
                "rel20": round(rel20, 2),
                "rel50": round(rel50, 2) if rel50 is not None else None,
                "band": "leading",
                "constituents": len(s_short),
            }

    # News catalyst layer (best-effort) — only fetch for the leading sectors.
    if leading:
        try:
            from services.scanners.sector_news import get_sector_news

            news = get_sector_news(list(leading.keys()))
            for sec, nv in (news or {}).items():
                if sec in leading:
                    leading[sec]["news"] = nv
        except Exception as exc:
            log.debug("sector_rotation.prepare: news layer skipped (%s)", exc)

    log.info(
        "sector_rotation.prepare: %d/%d stocks with returns, breadth20=%.2f%% | "
        "%d leading sectors (%s) | diag=%s",
        len(ret_short), len(sym_sector), uni_short,
        len(leading), ", ".join(sorted(leading.keys())) or "none",
        {k: diag[k] for k in sorted(diag, key=lambda x: diag[x], reverse=True)[:6]},
    )
    return {
        "leading_sectors": leading,
        "sym_sector": sym_sector,
        "sector_diag": diag,
    }


def evaluate(candles: list[dict], bars_per_year: int = 252,
             symbol: str | None = None, ctx: dict | None = None) -> dict | None:
    """Per-symbol hit test. Returns metric dict (raw) or None.

    Gate order: sector must be leading → stock must be a strong uptrend. All the
    generic scoring keys (mom_long_pct, pos52, stack, avg_vol, from_high_pct,
    flip_vol_x) are populated so scoring.py ranks it uniformly, plus sector/news
    context keys the UI displays and scoring.py uses for its rotation bonus.
    """
    if not ctx or not symbol:
        return None
    leading = ctx.get("leading_sectors") or {}
    if not leading:
        return None
    sector = (ctx.get("sym_sector") or {}).get(symbol)
    if not sector or sector not in leading:
        return None

    n = len(candles)
    if n < _MIN_BARS:
        return None
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    vols = [float(c.get("volume") or 0) for c in candles]

    last_close = closes[-1]
    if last_close <= 0:
        return None

    ema20 = ema(closes[-80:], 20) if n >= 20 else None
    ema50 = ema(closes[-160:], 50) if n >= 50 else None
    if ema20 is None or ema50 is None:
        return None

    stack = bool(last_close > ema20 > ema50)
    if _REQUIRE_STACK and not stack:
        return None

    def _mom(bars: int) -> float:
        if n > bars and closes[-1 - bars] > 0:
            return (closes[-1] / closes[-1 - bars] - 1.0) * 100.0
        return 0.0

    mom_long = _mom(20)     # ~1 month (D) / ~20 wk (W)
    if mom_long < _MIN_MOM_LONG:
        return None
    mom_short = _mom(5)

    # 52-week positioning
    win = min(max(bars_per_year, 1), n)
    hi = max(highs[-win:])
    lo = min(lows[-win:])
    pos52 = ((last_close - lo) / (hi - lo) * 100.0) if hi > lo else 0.0
    from_high = ((last_close - hi) / hi * 100.0) if hi > 0 else 0.0

    avg_vol = (sum(vols[-20:]) / min(20, len(vols))) if vols else 0.0
    last_vol = vols[-1] if vols else 0.0
    vol_x = (last_vol / avg_vol) if avg_vol > 0 else 0.0

    atr14 = atr_wilder(highs, lows, closes, 14)[-1] if n > 14 else 0.0
    # Protective stop: the higher of the recent 10-bar swing low and EMA20.
    swing_low = min(lows[-10:]) if n >= 10 else lo
    stop = max(swing_low, ema20 * 0.99)
    risk_pct = ((last_close - stop) / last_close * 100.0) if last_close > 0 else 0.0

    sv = leading[sector]
    news = sv.get("news") or {}

    return {
        # ── generic scoring keys (shared with scoring.py) ──
        "close": round(last_close, 2),
        "avg_vol": int(avg_vol),
        "flip_vol_x": round(vol_x, 2),
        "mom_short_pct": round(mom_short, 1),
        "mom_long_pct": round(mom_long, 1),
        "pos52": round(pos52, 1),
        "from_high_pct": round(from_high, 1),
        "stack": stack,
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "atr_pct": round(atr14 / last_close * 100.0, 2) if last_close > 0 else 0.0,
        "stop": round(stop, 2),
        "risk_to_stop_pct": round(risk_pct, 2),
        "dist_ema_pct": round((last_close - ema20) / ema20 * 100.0, 2),
        "date": str(candles[-1].get("date"))[:10],
        # ── sector-rotation context (UI + scoring bonus) ──
        "is_sector_rotation": True,
        "sector": sector,
        "sector_band": sv.get("band"),
        "sector_rel20_pct": sv.get("rel20"),
        "sector_rel50_pct": sv.get("rel50"),
        "news_heat": news.get("news_heat"),
        "news_tone": news.get("tone"),
        "news_articles": news.get("article_count"),
        "news_headline": news.get("top_headline"),
        "news_url": news.get("top_url"),
        "news_domain": news.get("top_domain"),
    }
