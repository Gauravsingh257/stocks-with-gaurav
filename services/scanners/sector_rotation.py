"""
services/scanners/sector_rotation.py — "Leading stock in a leading sector".

Scanner #2. Unlike the per-symbol Supertrend scan, this one is CROSS-SECTIONAL:
it first decides which sectors are rotating in, then only keeps strong stocks
that belong to those sectors.

Three real, honest layers (no synthetic data):
  1. SECTOR momentum  — services.sector_strength: real NSE sector-index relative
     strength vs NIFTY (leading / neutral / lagging).
  2. NEWS catalyst    — services.scanners.sector_news: GDELT DOC 2.0 per-sector
     news heat + tone (free, keyless, real-time). Best-effort; tilts score only.
  3. STOCK strength   — the stock itself must be in a confirmed uptrend (EMA
     stack) with positive 1-month momentum and be liquid.

`prepare(symbols)` runs the two cross-sectional layers ONCE per scan and returns
a context dict. `evaluate(candles, bars_per_year, symbol, ctx)` is then called
per symbol by the runner, gated against that shared context.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from services.scanners.indicators import ema, atr_wilder

log = logging.getLogger("services.scanners.sector_rotation")

# Stock-layer gates (env-tunable).
_REQUIRE_STACK = os.getenv("SECTOR_ROT_REQUIRE_STACK", "1").strip().lower() in ("1", "true", "yes")
_MIN_MOM_LONG = float(os.getenv("SECTOR_ROT_MIN_MOM", "0.0"))   # 1-month momentum floor (%)
_MIN_BARS = int(os.getenv("SECTOR_ROT_MIN_BARS", "60"))


def prepare(symbols: list[str]) -> dict[str, Any]:
    """Build the shared cross-sectional context for one scan run.

    Returns:
      {
        "leading_sectors": {sector: {rel20, rel50, band, news:{...}}},
        "sym_sector":      {symbol: sector},
        "generated_for":   "YYYY-MM-DD",
      }
    Failures degrade gracefully: if sector strength can't be computed we return an
    empty leading set (→ scan yields zero, never crashes the whole run).
    """
    try:
        from services.sector_strength import compute_sector_strength
    except Exception as exc:
        log.warning("sector_rotation.prepare: sector strength import failed (%s)", exc)
        return {"leading_sectors": {}, "sym_sector": {}}

    try:
        strength = compute_sector_strength()
    except Exception as exc:
        log.warning("sector_rotation.prepare: sector strength compute failed (%s)", exc)
        return {"leading_sectors": {}, "sym_sector": {}}

    sectors = strength.get("sectors") or {}
    leading = {
        sec: {
            "rel20": sv.get("rel_20d_pct"),
            "rel50": sv.get("rel_50d_pct"),
            "band": sv.get("band"),
        }
        for sec, sv in sectors.items()
        if sv.get("is_leading")
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

    # Map each scanned symbol to its sector once.
    sym_sector: dict[str, str] = {}
    try:
        from engine.swing import get_sector

        for sym in symbols:
            sym_sector[sym] = get_sector(sym)
    except Exception as exc:
        log.debug("sector_rotation.prepare: get_sector failed (%s)", exc)

    log.info(
        "sector_rotation.prepare: %d leading sectors (%s), %d symbols mapped",
        len(leading), ", ".join(sorted(leading.keys())) or "none", len(sym_sector),
    )
    return {
        "leading_sectors": leading,
        "sym_sector": sym_sector,
        "generated_for": strength.get("generated_for"),
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
