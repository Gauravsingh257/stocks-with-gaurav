from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pandas as pd

log = logging.getLogger("services.technical_scanner")


def _stable_unit(symbol: str, salt: str) -> float:
    raw = sha256(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


@dataclass(slots=True)
class TechnicalSnapshot:
    symbol: str
    trend_structure: float
    volume_expansion: float
    liquidity_sweep: float
    order_block_quality: float
    fvg_quality: float
    rsi_momentum: float
    macd_momentum: float
    mtf_alignment: float
    liquidity_score: float
    technical_score: float
    data_source: str = "hash"  # "hash" or "ohlc"

    def as_factors(self) -> dict[str, Any]:
        return {
            "trend_structure": round(self.trend_structure, 3),
            "volume_expansion": round(self.volume_expansion, 3),
            "liquidity_sweep": round(self.liquidity_sweep, 3),
            "order_block_quality": round(self.order_block_quality, 3),
            "fvg_quality": round(self.fvg_quality, 3),
            "rsi_momentum": round(self.rsi_momentum, 3),
            "macd_momentum": round(self.macd_momentum, 3),
            "mtf_alignment_1d_4h_1h": round(self.mtf_alignment, 3),
            "liquidity_score": round(self.liquidity_score, 3),
            "technical_score": round(self.technical_score, 3),
        }


def _weighted_score(parts: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in parts) or 1.0
    return sum(v * w for v, w in parts) / total_w


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 2:
        return 0.5
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta.clip(upper=0.0))
    avg_g = gain.rolling(period).mean()
    avg_l = loss.rolling(period).mean()
    rs = avg_g / avg_l.replace(0, 1e-12)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    v = float(rsi.iloc[-1])
    if pd.isna(v):
        return 0.5
    return _clamp01(v / 100.0)


def _macd_hist_norm(close: pd.Series) -> float:
    if len(close) < 35:
        return 0.5
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd_line = ema12 - ema26
    signal = _ema(macd_line, 9)
    hist = (macd_line - signal).iloc[-1]
    if pd.isna(hist):
        return 0.5
    # Normalize vs recent ATR proxy (rolling std of close)
    scale = close.pct_change().rolling(20).std().iloc[-1] * float(close.iloc[-1])
    scale = max(scale, float(close.iloc[-1]) * 0.001)
    return _clamp01(0.5 + float(hist) / (2.5 * scale))


def _normalize_ohlc_df(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if "close" not in out.columns:
        return None
    return out


def _snapshot_from_ohlc(symbol: str, df: pd.DataFrame) -> TechnicalSnapshot | None:
    frame = _normalize_ohlc_df(df)
    if frame is None or len(frame) < 60:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce") if "high" in frame.columns else close
    low = pd.to_numeric(frame["low"], errors="coerce") if "low" in frame.columns else close
    vol = pd.to_numeric(frame["volume"], errors="coerce") if "volume" in frame.columns else pd.Series([0.0] * len(close))
    if close.isna().all():
        return None
    sma50 = close.rolling(50).mean().iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    last = float(close.iloc[-1])
    if pd.isna(sma50) or pd.isna(sma20) or last <= 0:
        return None
    trend = _clamp01(0.35 + 0.65 * ((last - float(sma50)) / max(float(sma50), 1e-9) + 0.05) / 0.25)
    vol_ma = vol.rolling(20).mean().iloc[-1]
    last_v = float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else 0.0
    vol_exp = _clamp01(0.35 + 0.65 * (last_v / max(float(vol_ma), 1.0)))
    rng = (high - low).rolling(14).mean().iloc[-1]
    prev_rng = (high - low).rolling(14).mean().shift(1).iloc[-1]
    sweep = _clamp01(0.35 + 0.65 * (float(rng) / max(float(prev_rng), 1e-9) / 2.0)) if not pd.isna(rng) else 0.5
    rsi_n = _rsi(close, 14)
    macd_n = _macd_hist_norm(close)
    # OB / FVG proxies: recent range expansion vs body
    o_series = pd.to_numeric(frame["open"], errors="coerce") if "open" in frame.columns else close
    body = (close - o_series).abs()
    br = (body / (high - low).replace(0, 1e-9)).rolling(5).mean().iloc[-1]
    ob_q = _clamp01(0.35 + 0.65 * (1.0 - float(br))) if not pd.isna(br) else 0.5
    fvg_q = _clamp01(0.35 + 0.65 * float(rng) / max(last * 0.02, 1e-9)) if not pd.isna(rng) else 0.5
    mtf = _clamp01(0.4 + 0.6 * (1.0 if last >= float(sma20) else 0.3))
    liq = _clamp01(0.35 + 0.65 * min(1.0, (last_v * last) / max(1e6, last * 1000)))

    score = _weighted_score(
        [
            (trend, 0.18),
            (vol_exp, 0.14),
            (sweep, 0.10),
            (ob_q, 0.14),
            (fvg_q, 0.12),
            (rsi_n, 0.10),
            (macd_n, 0.10),
            (mtf, 0.12),
        ]
    )

    return TechnicalSnapshot(
        symbol=symbol,
        trend_structure=trend,
        volume_expansion=vol_exp,
        liquidity_sweep=sweep,
        order_block_quality=ob_q,
        fvg_quality=fvg_q,
        rsi_momentum=rsi_n,
        macd_momentum=macd_n,
        mtf_alignment=mtf,
        liquidity_score=liq,
        technical_score=score,
        data_source="ohlc",
    )


def _snapshot_hash(symbol: str) -> TechnicalSnapshot:
    trend = 0.45 + (_stable_unit(symbol, "trend") * 0.55)
    volume = 0.40 + (_stable_unit(symbol, "volume") * 0.60)
    sweep = 0.35 + (_stable_unit(symbol, "sweep") * 0.65)
    ob = 0.35 + (_stable_unit(symbol, "ob") * 0.65)
    fvg = 0.35 + (_stable_unit(symbol, "fvg") * 0.65)
    rsi = 0.30 + (_stable_unit(symbol, "rsi") * 0.70)
    macd = 0.30 + (_stable_unit(symbol, "macd") * 0.70)
    mtf = 0.40 + (_stable_unit(symbol, "mtf") * 0.60)
    liquidity = 0.50 + (_stable_unit(symbol, "liq") * 0.50)
    score = _weighted_score(
        [
            (trend, 0.18),
            (volume, 0.14),
            (sweep, 0.10),
            (ob, 0.14),
            (fvg, 0.12),
            (rsi, 0.10),
            (macd, 0.10),
            (mtf, 0.12),
        ]
    )
    return TechnicalSnapshot(
        symbol=symbol,
        trend_structure=trend,
        volume_expansion=volume,
        liquidity_sweep=sweep,
        order_block_quality=ob,
        fvg_quality=fvg,
        rsi_momentum=rsi,
        macd_momentum=macd,
        mtf_alignment=mtf,
        liquidity_score=liquidity,
        technical_score=score,
    )


def _use_hash_for_universe_ranking() -> bool:
    """
    Full-universe OHLC technicals are too slow for HTTP-bound scans (hundreds of yfinance calls).
    Default: hash-based scores for cross-sectional ranking only; real OHLC is applied in
    services.research_levels when materializing top picks (see ranking_engine._collect_ideas_from_pool).
    Set RESEARCH_TECH_OHLC_FULL_UNIVERSE=1 to fetch daily bars for every symbol (dev/stress only).
    """
    if os.getenv("TECH_SCANNER_USE_HASH_ONLY", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("RESEARCH_TECH_OHLC_FULL_UNIVERSE", "").lower() in ("1", "true", "yes"):
        return False
    return True


async def scan_technical(symbols: list[str]) -> dict[str, TechnicalSnapshot]:
    """
    Cross-sectional technical scores for ranking.

    Default uses deterministic hash scores (fast). Real entry/SL/targets still come from OHLC in
    research_levels for the finalist pool. Enable RESEARCH_TECH_OHLC_FULL_UNIVERSE=1 for per-symbol
    OHLC technicals (slow; not recommended behind a web request).
    """
    if _use_hash_for_universe_ranking():
        return {s: _snapshot_hash(s) for s in symbols}

    from data.ingestion import DataIngestion

    src = os.getenv("RESEARCH_DATA_SOURCE", "yfinance")
    ingestion = DataIngestion(source=src)
    days = int(os.getenv("RESEARCH_FETCH_DAYS", "420"))
    out: dict[str, TechnicalSnapshot] = {}

    for symbol in symbols:
        try:
            df = ingestion.fetch_historical(symbol, interval="day", days=days)
        except Exception as exc:
            log.debug("OHLC fetch failed for %s: %s", symbol, exc)
            df = pd.DataFrame()

        snap = _snapshot_from_ohlc(symbol, df)
        if snap is None:
            snap = _snapshot_hash(symbol)
        out[symbol] = snap
    return out
