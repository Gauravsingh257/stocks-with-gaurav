"""
Content Creation / agents / stock_picker_agent.py

StockPickerAgent v2 — Real SMC swing scan on FNO universe.

Uses the same engine/swing.py scoring as the live swing scanner:
  - Weekly trend (BOS/HH/LL)
  - Daily structure (BOS/CHoCH)
  - Order blocks + FVG zones
  - Relative strength vs Nifty
  - Volume accumulation/distribution
  - Risk-reward gating (≥2.5 RR, score ≥ quality bar)

Scans ~130 FNO stocks via yfinance bulk download (~5-8s).
Falls back to sector-momentum scoring if scan yields < 3 picks.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yfinance as yf

from agents.base import BaseContentAgent
from models.contracts import (
    MarketAnalysis,
    MarketData,
    PipelineMode,
    Sentiment,
    StockPick,
    StockPicks,
)

log = logging.getLogger("content_creation.agents.stock_picker")

# ═══════════════════════════════════════════════════════════════════════════
#  Path setup — add project root so engine/ is importable
# ═══════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # Trading Algo/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════════
#  UNIVERSE
# ═══════════════════════════════════════════════════════════════════════════

def _load_fno_universe() -> list[str]:
    """Load FNO stock symbols from stock_universe_fno.json."""
    fno_path = _PROJECT_ROOT / "stock_universe_fno.json"
    if not fno_path.exists():
        log.warning("stock_universe_fno.json not found at %s", fno_path)
        return []
    with open(fno_path) as f:
        raw = json.load(f)
    # Format: ["NSE:HDFCBANK", "NSE:SBIN", ...] → ["HDFCBANK", "SBIN", ...]
    symbols = []
    for item in raw:
        if isinstance(item, str):
            sym = item.replace("NSE:", "").strip()
            if sym:
                symbols.append(sym)
    return symbols


# ═══════════════════════════════════════════════════════════════════════════
#  YFINANCE DATA ADAPTER — bulk download, convert to engine candle format
# ═══════════════════════════════════════════════════════════════════════════

def _yf_to_candles(df) -> list[dict]:
    """Convert yfinance DataFrame to list of candle dicts for engine/swing.py."""
    if df is None or df.empty:
        return []
    # Flatten multi-level columns from yfinance (e.g. ('Close', '^NSEI'))
    if df.columns.nlevels > 1:
        df = df.copy()
        df.columns = df.columns.droplevel(1)
    candles = []
    for _, row in df.iterrows():
        try:
            candles.append({
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0)),
            })
        except (ValueError, TypeError):
            continue
    return candles


def _bulk_fetch_ohlc(symbols: list[str], period: str = "6mo", interval: str = "1d") -> dict:
    """
    Bulk-fetch OHLC data from yfinance for multiple symbols.
    Returns {symbol: DataFrame}.
    """
    if not symbols:
        return {}
    # yfinance tickers: SYMBOL.NS for NSE
    yf_tickers = [f"{s}.NS" for s in symbols]
    ticker_str = " ".join(yf_tickers)
    try:
        data = yf.download(
            ticker_str,
            period=period,
            interval=interval,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        if data is None or data.empty:
            return {}

        result = {}
        for sym, yf_sym in zip(symbols, yf_tickers):
            try:
                if len(yf_tickers) == 1:
                    df = data.copy()
                elif data.columns.nlevels > 1 and yf_sym in data.columns.get_level_values(0):
                    df = data[yf_sym].copy()
                else:
                    continue
                df = df.dropna(subset=["Close"])
                if not df.empty:
                    result[sym] = df
            except Exception:
                continue
        return result
    except Exception as e:
        log.warning("Bulk yfinance download failed: %s", e)
        return {}


def _fetch_weekly_from_daily(daily_df):
    """Resample daily data to weekly candles."""
    if daily_df is None or daily_df.empty:
        return None
    try:
        weekly = daily_df.resample("W").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }).dropna(subset=["Close"])
        return weekly if not weekly.empty else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  SECTOR FALLBACK MAPS
# ═══════════════════════════════════════════════════════════════════════════

_SECTOR_MAP: dict[str, str] = {
    "RELIANCE": "Energy", "TCS": "IT", "HDFCBANK": "Bank", "INFY": "IT",
    "ICICIBANK": "Bank", "BHARTIARTL": "Telecom", "SBIN": "Bank", "ITC": "FMCG",
    "TATAMOTORS": "Auto", "MARUTI": "Auto", "SUNPHARMA": "Pharma", "WIPRO": "IT",
    "HCLTECH": "IT", "ADANIENT": "Conglomerate", "TATASTEEL": "Metal",
    "JSWSTEEL": "Metal", "LT": "Infra", "AXISBANK": "Bank", "BAJFINANCE": "NBFC",
    "TITAN": "Consumer", "ULTRACEMCO": "Cement", "DRREDDY": "Pharma",
    "POWERGRID": "Power", "NTPC": "Power", "M&M": "Auto", "HINDALCO": "Metal",
    "ONGC": "Energy", "COALINDIA": "Mining", "TECHM": "IT", "ASIANPAINT": "Consumer",
}

_SECTOR_LEADERS = {
    "IT": "TCS", "Bank": "HDFCBANK", "Energy": "RELIANCE", "Auto": "MARUTI",
    "Pharma": "SUNPHARMA", "Metal": "TATASTEEL", "FMCG": "ITC",
    "Telecom": "BHARTIARTL", "NBFC": "BAJFINANCE", "Consumer": "TITAN",
    "Infra": "LT", "Power": "NTPC", "Cement": "ULTRACEMCO",
    "Conglomerate": "ADANIENT", "Mining": "COALINDIA",
}

_STOCK_NAMES: dict[str, str] = {
    "RELIANCE": "Reliance Industries", "TCS": "Tata Consultancy Services",
    "HDFCBANK": "HDFC Bank", "INFY": "Infosys", "ICICIBANK": "ICICI Bank",
    "BHARTIARTL": "Bharti Airtel", "SBIN": "State Bank of India", "ITC": "ITC Limited",
    "TATAMOTORS": "Tata Motors", "MARUTI": "Maruti Suzuki", "SUNPHARMA": "Sun Pharma",
    "WIPRO": "Wipro", "HCLTECH": "HCL Technologies", "ADANIENT": "Adani Enterprises",
    "TATASTEEL": "Tata Steel", "JSWSTEEL": "JSW Steel", "LT": "Larsen & Toubro",
    "AXISBANK": "Axis Bank", "BAJFINANCE": "Bajaj Finance", "TITAN": "Titan Company",
    "ULTRACEMCO": "UltraTech Cement", "DRREDDY": "Dr. Reddy's",
    "POWERGRID": "Power Grid Corp", "NTPC": "NTPC Limited",
    "M&M": "Mahindra & Mahindra", "HINDALCO": "Hindalco Industries",
    "ONGC": "ONGC", "COALINDIA": "Coal India", "TECHM": "Tech Mahindra",
    "ASIANPAINT": "Asian Paints",
}


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT
# ═══════════════════════════════════════════════════════════════════════════

class StockPickerAgent(BaseContentAgent):
    name = "StockPickerAgent"
    description = "SMC swing scan on FNO universe — real technical scoring"

    def run(
        self,
        *,
        market_data: MarketData,
        analysis: MarketAnalysis,
    ) -> StockPicks:
        # ── 1. Try real SMC scan ──────────────────────────────────────────
        smc_picks = self._run_smc_scan(market_data, analysis)

        if len(smc_picks) >= 3:
            log.info(
                "StockPicker: %d SMC picks — %s",
                len(smc_picks),
                [(p.symbol, p.setup_type) for p in smc_picks],
            )
            return StockPicks(
                picks=smc_picks[:3],
                selection_criteria="SMC swing scan: weekly trend + daily structure + OB/FVG + RS + volume",
                market_context=analysis.summary or "",
            )

        # ── 2. Fallback: sector momentum (if SMC < 3) ────────────────────
        log.info("SMC scan yielded %d picks, supplementing with sector picks", len(smc_picks))
        fallback_picks = self._sector_momentum_picks(
            market_data, analysis, exclude={p.symbol for p in smc_picks}
        )
        combined = smc_picks + fallback_picks
        combined = combined[:3]

        log.info(
            "StockPicker: %d picks (SMC=%d + fallback=%d) — %s",
            len(combined), len(smc_picks), len(combined) - len(smc_picks),
            [(p.symbol, p.setup_type) for p in combined],
        )
        return StockPicks(
            picks=combined,
            selection_criteria="SMC swing + sector momentum fallback",
            market_context=analysis.summary or "",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  CORE: SMC SWING SCAN
    # ══════════════════════════════════════════════════════════════════════

    def _run_smc_scan(self, data: MarketData, analysis: MarketAnalysis) -> list[StockPick]:
        """Run real SMC swing scan on FNO universe using engine/swing.py scoring."""
        try:
            from engine.swing import score_swing_candidate
            from engine import config as cfg
        except ImportError as e:
            log.warning("Cannot import engine/swing.py: %s — using fallback", e)
            return []

        # Load universe
        symbols = _load_fno_universe()
        if not symbols:
            log.warning("No FNO universe loaded")
            return []

        log.info("SMC scan: fetching data for %d FNO stocks...", len(symbols))

        # Bulk fetch daily data (6mo) — yfinance does this in 1-2 calls
        daily_data_map = _bulk_fetch_ohlc(symbols, period="6mo", interval="1d")
        if not daily_data_map:
            log.warning("No daily data fetched")
            return []

        log.info("SMC scan: got daily data for %d/%d symbols", len(daily_data_map), len(symbols))

        # Fetch Nifty daily for relative strength calc
        nifty_candles: list[dict] = []
        try:
            nifty_raw = yf.download("^NSEI", period="6mo", interval="1d", progress=False)
            if nifty_raw is not None and not nifty_raw.empty:
                nifty_candles = _yf_to_candles(nifty_raw)
        except Exception:
            pass

        if not nifty_candles:
            log.warning("No Nifty data for RS calculation — proceeding without it")

        # Lower quality bar for content (live scan uses 7)
        original_min_quality = getattr(cfg, "MIN_SWING_QUALITY", 7)
        cfg.MIN_SWING_QUALITY = 5  # Relax for content picks

        candidates = []
        scanned = 0

        for symbol, daily_df in daily_data_map.items():
            try:
                daily_candles = _yf_to_candles(daily_df)
                if len(daily_candles) < 30:
                    continue

                weekly_df = _fetch_weekly_from_daily(daily_df)
                weekly_candles = _yf_to_candles(weekly_df)
                if len(weekly_candles) < 12:
                    continue

                result = score_swing_candidate(symbol, daily_candles, weekly_candles, nifty_candles)
                if result:
                    candidates.append(result)
                scanned += 1
            except Exception:
                continue

        # Restore
        cfg.MIN_SWING_QUALITY = original_min_quality

        log.info("SMC scan: %d scanned, %d qualifying candidates", scanned, len(candidates))

        if not candidates:
            return []

        # Sort by score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Diversity: max 1 per sector
        seen_sectors: set[str] = set()
        diverse: list[dict] = []
        for c in candidates:
            sector = c.get("sector", "Unknown")
            if sector not in seen_sectors:
                seen_sectors.add(sector)
                diverse.append(c)
            if len(diverse) >= 4:
                break

        # Convert to StockPick
        picks: list[StockPick] = []
        for c in diverse:
            sym = c["symbol"]
            direction = c.get("direction", "LONG")
            daily_struct = c.get("daily_structure", "")
            setup = self._map_setup_type(direction, daily_struct, c.get("score", 0))

            picks.append(StockPick(
                symbol=sym,
                name=_STOCK_NAMES.get(sym, sym),
                sector=c.get("sector", _SECTOR_MAP.get(sym, "Unknown")),
                setup_type=setup,
                rationale="; ".join(c.get("reasons", [])[:3]),
                price=c.get("entry", 0.0),
                stop_loss=c.get("sl", 0.0),
                target=c.get("target", 0.0),
            ))

        return picks

    def _map_setup_type(self, direction: str, daily_structure: str, score: int) -> str:
        """Map SMC result to display-friendly setup type."""
        if "BOS" in str(daily_structure):
            if direction == "LONG":
                return "BREAKOUT" if score >= 8 else "TREND"
            return "BREAKDOWN"
        elif "CHOCH" in str(daily_structure):
            return "REVERSAL"
        return "SWING SETUP"

    # ══════════════════════════════════════════════════════════════════════
    #  FALLBACK: Sector momentum picks
    # ══════════════════════════════════════════════════════════════════════

    def _sector_momentum_picks(
        self, data: MarketData, analysis: MarketAnalysis, exclude: set[str]
    ) -> list[StockPick]:
        """Pick sector leaders from strongest moving sectors."""
        sector_pct: dict[str, float] = {}
        for s in data.sectors:
            sector_pct[s.name] = s.change_pct

        # Sort sectors by absolute movement
        sorted_sectors = sorted(sector_pct.items(), key=lambda x: abs(x[1]), reverse=True)

        picks: list[StockPick] = []
        for sector_name, pct in sorted_sectors:
            if abs(pct) < 0.3:
                continue
            leader = _SECTOR_LEADERS.get(sector_name)
            if not leader or leader in exclude:
                continue

            price, sl, target = self._fetch_stock_levels(leader)
            direction = "+" if pct > 0 else ""
            setup = "MOMENTUM" if pct > 0 else "REVERSAL WATCH"
            reason = f"{sector_name} sector {direction}{pct:.1f}% — sector rotation candidate"

            picks.append(StockPick(
                symbol=leader,
                name=_STOCK_NAMES.get(leader, leader),
                sector=sector_name,
                setup_type=setup,
                rationale=reason,
                price=price,
                stop_loss=sl,
                target=target,
            ))
            exclude.add(leader)

            if len(picks) >= 3:
                break
        return picks

    def _fetch_stock_levels(self, symbol: str) -> tuple[float, float, float]:
        """Fetch price + compute SL/target from recent high/low via yfinance."""
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            hist = ticker.history(period="1mo")
            if hist.empty:
                return 0.0, 0.0, 0.0

            valid_close = hist["Close"].dropna()
            if valid_close.empty:
                return 0.0, 0.0, 0.0

            price = round(float(valid_close.iloc[-1]), 2)
            recent_low = round(float(hist["Low"].dropna().min()), 2)
            recent_high = round(float(hist["High"].dropna().max()), 2)
            sl = round(recent_low, 2)
            target = round(recent_high, 2)
            return price, sl, target
        except Exception as e:
            log.warning("Failed to fetch levels for %s: %s", symbol, e)
            return 0.0, 0.0, 0.0
