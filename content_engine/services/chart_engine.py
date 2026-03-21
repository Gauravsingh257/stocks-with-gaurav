"""
content_engine/services/chart_engine.py

Chart images — **two modes**:

1. **Generated (default)** — OHLC from **Yahoo Finance** (`yfinance`), candles drawn
   with **mplfinance/matplotlib**. Styled like a dark TradingView chart, but it is
   **not** rendered by tradingview.com and **not** Zerodha/Kite.

2. **Your TradingView (or any) screenshot** — place a PNG/JPG named ``{strategy_id}.png``
   under ``content_engine/assets/tv_charts/`` (or set env ``CONTENT_TV_CHART_DIR``).
   When that file exists, **full posts** and **cheatsheet strips** use your bitmap
   instead of the generated chart (with ENTRY/SL/TARGET overlays on top).

Public API:
    create_real_chart_post(strategy: dict, symbol: str | None = None, ...) -> str
    pick_random_chart_instrument() -> str
    render_cheatsheet_chart_strip(strategy_id, width, height) -> Image | None
    create_post_from_screenshot(strategy, image_path) -> str

Supported strategy overlays (keyed on strategy["id"]):
    order_block, fvg, vwap_strategy, trend_pullback, orb,
    range_breakout, sr_flip, trendline_break, consolidation_breakout,
    momentum_scalp, liquidity_grab, breaker_block
"""

from __future__ import annotations

import logging
import os
import random
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import mplfinance as mpf
import numpy as np
import yfinance as yf
from PIL import Image, ImageDraw, ImageFont, ImageFilter

log = logging.getLogger("content_engine.chart_engine")

# ── Output directory ──────────────────────────────────────────────────────────
_HERE    = Path(__file__).parent.parent
_OUT_DIR = _HERE / "generated_posts"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Canvas layout ─────────────────────────────────────────────────────────────
CANVAS_W, CANVAS_H = 1080, 1080
HEADER_H            = 110          # hook + title
FOOTER_H            = 170          # caption + branding
CHART_H             = CANVAS_H - HEADER_H - FOOTER_H   # 800px

# ── Random chart variety — indices + liquid NSE names (Yahoo Finance tickers) ─
CHART_INSTRUMENT_POOL: tuple[str, ...] = (
    "^NSEI",
    "^NSEBANK",
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "BHARTIARTL.NS",
    "KOTAKBANK.NS",
    "LT.NS",
    "ITC.NS",
    "WIPRO.NS",
    "TATASTEEL.NS",
    "TATAMOTORS.NS",
    "AXISBANK.NS",
    "MARUTI.NS",
    "SUNPHARMA.NS",
)


def pick_random_chart_instrument() -> str:
    """Pick a random Yahoo symbol so each chart post can show different price action."""
    return random.choice(CHART_INSTRUMENT_POOL)


def chart_display_symbol(yahoo_ticker: str) -> str:
    """
    Short label for the chart tag (e.g. ``^NSEI`` → ``NSEI``, ``RELIANCE.NS`` → ``RELIANCE``).
    """
    s = (yahoo_ticker or "").strip()
    if not s:
        return "NSEI"
    su = s.upper()
    if su.endswith(".NS"):
        return su[:-3].replace("-", "")
    if s.startswith("^"):
        core = su[1:]
        if core == "NSEBANK":
            return "BANKNIFTY"
        return core
    return su


# ── TradingView-inspired dark palette (primary) ───────────────────────────────
_TV = {
    # backgrounds
    "bg":          "#0b0f17",      # deep navy-black (TV default)
    "chart_bg":    "#0d1117",      # slightly lighter chart pane
    "panel_bg":    "#0a0e15",      # volume panel
    "header_bg":   "#0f1923",      # header bar
    "footer_bg":   "#080c12",      # footer bar

    # candles
    "candle_up":   "#00c853",      # TV green
    "candle_dn":   "#ff1744",      # TV red
    "wick":        "#606060",      # thin mid-grey wicks

    # grid / borders
    "grid":        "#161b26",      # very subtle grid lines
    "border":      "#1e2738",      # axis border colour
    "divider":     "#1a2234",

    # text
    "fg":          "#d1d4dc",      # TV primary text
    "muted":       "#4a5568",      # tick labels, secondary
    "label_bg":    "#1c2436",      # price-tag background

    # indicator colours
    "accent":      "#2196f3",      # VWAP / accent blue
    "ema":         "#ff9800",      # EMA line orange
    "yellow":      "#ffc107",      # OB zones
    "purple":      "#9c27b0",      # FVG

    # trade level colours
    "entry":       "#00c853",      # green
    "sl":          "#ff1744",      # red
    "target":      "#2196f3",      # blue

    # bar / branding
    "bar_bg":      "#0f1923",
    "accent2":     "#00bcd4",      # cyan highlights
}

# ── Light palette (fallback, used 30% of time) ────────────────────────────────
_LIGHT = {
    "bg":          "#f0f3fa",
    "chart_bg":    "#f9faff",
    "panel_bg":    "#eef1f8",
    "header_bg":   "#1a237e",
    "footer_bg":   "#e8ecf5",
    "candle_up":   "#00897b",
    "candle_dn":   "#d32f2f",
    "wick":        "#9e9e9e",
    "grid":        "#e0e5f0",
    "border":      "#c5cfe0",
    "divider":     "#d0d8ec",
    "fg":          "#1a1a2e",
    "muted":       "#9e9e9e",
    "label_bg":    "#dce3f5",
    "accent":      "#1565c0",
    "ema":         "#e65100",
    "yellow":      "#f57c00",
    "purple":      "#6a1b9a",
    "entry":       "#00897b",
    "sl":          "#d32f2f",
    "target":      "#1565c0",
    "bar_bg":      "#1a237e",
    "accent2":     "#0097a7",
}

# ── Hook pool ─────────────────────────────────────────────────────────────────
_HOOKS = [
    "SMART MONEY SECRET",
    "MOST TRADERS FAIL HERE",
    "HIGH PROBABILITY SETUP",
    "DON'T MISS THIS SETUP",
    "PRO TRADER SETUP",
    "INSTITUTIONS LOVE THIS",
    "WYCKOFF LOGIC IN ACTION",
    "RETAIL TRAP — WATCH OUT",
    "HOW SMART MONEY MOVES",
    "RISK:REWARD GOLD ZONE",
]

_CLOSERS = [
    "Wait for confirmation before entering.",
    "Avoid early entries — patience pays.",
    "Risk management is non-negotiable.",
    "Trade with the trend, not against it.",
    "One good trade beats five bad ones.",
    "Small SL + big target = your edge.",
    "Let price come to your zone.",
    "No confirmation = no trade.",
]

# ── Font helpers ──────────────────────────────────────────────────────────────
_WIN_FONT_DIR = Path("C:/Windows/Fonts")
_LINUX_DIRS   = [Path("/usr/share/fonts/truetype/dejavu"),
                 Path("/usr/share/fonts/truetype/liberation"),
                 Path("/usr/share/fonts/truetype")]
_FONT_MAP     = {
    "bold":    ["arialbd.ttf", "LiberationSans-Bold.ttf",    "DejaVuSans-Bold.ttf"],
    "regular": ["arial.ttf",   "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
}


def _find_font(style: str) -> Path | None:
    for name in _FONT_MAP.get(style, _FONT_MAP["regular"]):
        for d in [_WIN_FONT_DIR] + _LINUX_DIRS:
            p = d / name
            if p.exists():
                return p
    return None


def _font(style: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    p = _find_font(style)
    if p:
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            pass
    return ImageFont.load_default()


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class ChartError(RuntimeError):
    """Raised when chart generation fails."""


def _resolve_tv_chart_file(strategy_id: str) -> Path | None:
    """
    If the user exported a chart from TradingView (or any platform) as a PNG/JPG,
    save it as ``{strategy_id}.png`` under:

        content_engine/assets/tv_charts/

    Or set ``CONTENT_TV_CHART_DIR`` to a folder containing those files.

    Filenames must match strategy ids (e.g. ``fvg.png``, ``order_block.png``).
    """
    override = os.environ.get("CONTENT_TV_CHART_DIR", "").strip()
    base = Path(override) if override else (_HERE / "assets" / "tv_charts")
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = base / f"{strategy_id}{ext}"
        if p.exists():
            return p
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_yahoo_chart_symbol(symbol: str) -> str:
    """
    Map display tags (NSEI, NIFTY, …) to Yahoo tickers used for OHLC overlay alignment.
    """
    s = symbol.strip()
    if s.startswith("^"):
        return s
    u = s.upper().replace(" ", "")
    if u in ("NSEI", "NIFTY", "NIFTY50", "CNXNIFTY"):
        return "^NSEI"
    if u in ("BANKNIFTY", "NIFTYBANK", "CNXBANK"):
        return "^NSEBANK"
    return f"^{s.lstrip('^')}"


def _yahoo_symbol_for_chart_fetch(tag: str) -> str:
    """
    Map chart tag / display name to a Yahoo ticker suitable for ``_fetch_ohlc``.
    e.g. ``RELIANCE`` → ``RELIANCE.NS``, ``NSEI`` → ``^NSEI``.
    """
    t = (tag or "").strip()
    if not t:
        return "^NSEI"
    if t.startswith("^") or t.upper().endswith(".NS"):
        return t
    u = t.upper().replace(" ", "")
    if u in ("NSEI", "NIFTY", "NIFTY50", "CNXNIFTY"):
        return "^NSEI"
    if u in ("BANKNIFTY", "NIFTYBANK", "CNXBANK"):
        return "^NSEBANK"
    if "." in t:
        return t
    return f"{u}.NS"


def _fetch_ohlc(
    symbol: str,
    interval: str = "5m",
    period: str = "1d",
    *,
    random_window: bool = False,
    window_bars: int = 40,
) -> Any:
    """
    Fetch 5-minute OHLC from Yahoo Finance.

    If ``random_window`` is True, pulls longer history (5d → 1mo → 3mo) and uses a
    **random contiguous slice** of ``window_bars`` candles so each run can show a
    different historical segment.
    """
    import pandas as pd

    try:
        ticker = yf.Ticker(symbol)
        if random_window:
            df = None
            for p in ("5d", "1mo", "3mo"):
                df = ticker.history(period=p, interval=interval)
                if df is not None and len(df) >= window_bars + 5:
                    break
            if df is None or len(df) < window_bars:
                df = ticker.history(period="max", interval=interval)
        else:
            df = ticker.history(period=period, interval=interval)

        if df is None or df.empty:
            raise ChartError(f"No data returned for {symbol}")
        df = df.dropna(how="any")
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        n = len(df)
        if random_window and n > window_bars:
            start = random.randint(0, n - window_bars)
            df = df.iloc[start : start + window_bars].copy()
        else:
            df = df.tail(window_bars)

        if len(df) < 5:
            raise ChartError(f"Insufficient candles: {len(df)} for {symbol}")

        vol_max = float(df["Volume"].max())
        if vol_max < 1.0:
            df = df.copy()
            df["Volume"] = 0
        return df
    except ChartError:
        raise
    except Exception as exc:
        raise ChartError(f"yfinance fetch failed for {symbol}: {exc}") from exc


def _has_real_volume(df: Any) -> bool:
    """Return True only when volume data is meaningful (not near-zero)."""
    return float(df["Volume"].max()) >= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# INDICATOR CALCULATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_vwap(df: Any) -> Any:
    tp   = (df["High"] + df["Low"] + df["Close"]) / 3
    cvol = df["Volume"].cumsum()
    return (tp * df["Volume"]).cumsum() / cvol.replace(0, 1)


def _calc_ema(df: Any, period: int = 21) -> Any:
    return df["Close"].ewm(span=period, adjust=False).mean()


def _atr_like(df: Any, period: int = 14) -> float:
    """Average true range (proxy) for buffer sizing."""
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values
    if len(h) < 2:
        return float(max(np.max(h) - np.min(l), 1e-9))
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    tail = tr[-min(period, len(tr)) :]
    return float(np.mean(tail))


def _fractal_swings(
    df: Any,
    left: int = 2,
    right: int = 2,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Local swing highs / swing lows (fractal) on OHLC.
    Returns (list of (bar_index, price), ...) for highs and lows.
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(highs)
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(left, n - right):
        wh = highs[i - left : i + right + 1]
        wl = lows[i - left : i + right + 1]
        if highs[i] >= np.max(wh):
            swing_highs.append((i, float(highs[i])))
        if lows[i] <= np.min(wl):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def _trade_levels_legacy_spread(df: Any, bias: str) -> tuple[float, float, float]:
    """Fallback: fixed % of session range from last close (legacy behaviour)."""
    price = float(df["Close"].iloc[-1])
    spread = float(df["High"].max() - df["Low"].min())
    if spread <= 0:
        spread = price * 0.001
    entry = price
    if bias == "BULLISH":
        sl = entry - spread * 0.07
        target = entry + spread * 0.20
    else:
        sl = entry + spread * 0.07
        target = entry - spread * 0.20
    return entry, sl, target


def trade_levels_from_swings(df: Any, strategy_id: str) -> tuple[float, float, float]:
    """
    ENTRY / SL / TARGET from **recent swing structure** (not fixed % of range).

    - **SL** — beyond the latest relevant swing low (bullish) or swing high (bearish).
    - **ENTRY** — near current price, biased slightly toward last swing (retest).
    - **TARGET** — prior swing high/low (liquidity) or RR extension if missing.

    Falls back to :func:`_trade_levels_legacy_spread` if swings are unusable.
    """
    meta = _STRATEGY_META.get(strategy_id, {"bias": "BULLISH"})
    bias = meta["bias"]

    ph = float(df["High"].max())
    pl = float(df["Low"].min())
    rng = max(ph - pl, ph * 1e-6)
    close = float(df["Close"].iloc[-1])
    atr = _atr_like(df)
    buf = max(rng * 0.005, atr * 0.14, ph * 1e-5)

    sh_list, sl_list = _fractal_swings(df, left=2, right=2)

    if bias == "BULLISH":
        if not sl_list:
            return _trade_levels_legacy_spread(df, bias)
        # Most recent swing low = structural invalidation
        _sli, slp = sl_list[-1]
        sl = slp - buf
        # Retest + current: weighted toward live price, still inside logical zone
        entry = float(np.clip(0.58 * close + 0.42 * slp, sl + rng * 0.015, ph - rng * 0.01))
        # Liquidity above: swing highs after this low; else prior high / RR
        highs_after = [p for i, p in sh_list if i > _sli and p > entry]
        if highs_after:
            target = max(highs_after) + buf * 0.35
        else:
            highs_before = [p for i, p in sh_list if i <= _sli]
            if highs_before:
                target = max(highs_before) + buf * 0.25
            else:
                target = entry + max(1.65 * (entry - sl), rng * 0.07)
        if target <= entry:
            target = entry + max(1.5 * (entry - sl), rng * 0.05)
        if sl >= entry:
            sl = min(slp, pl) - buf
        if sl >= entry:
            return _trade_levels_legacy_spread(df, bias)
    else:
        if not sh_list:
            return _trade_levels_legacy_spread(df, bias)
        _shi, shp = sh_list[-1]
        sl = shp + buf
        entry = float(np.clip(0.58 * close + 0.42 * shp, pl + rng * 0.01, sl - rng * 0.015))
        lows_after = [p for i, p in sl_list if i > _shi and p < entry]
        if lows_after:
            target = min(lows_after) - buf * 0.35
        else:
            lows_before = [p for i, p in sl_list if i <= _shi]
            if lows_before:
                target = min(lows_before) - buf * 0.25
            else:
                target = entry - max(1.65 * (sl - entry), rng * 0.07)
        if target >= entry:
            target = entry - max(1.5 * (sl - entry), rng * 0.05)
        if sl <= entry:
            sl = max(shp, ph) + buf
        if sl <= entry:
            return _trade_levels_legacy_spread(df, bias)

    return entry, sl, target


def _y_frac_for_price(price: float, ph: float, pl: float, pad_frac: float = 0.02) -> float:
    """Map price to vertical fraction (0=top=high) for screenshot overlays."""
    pad = (ph - pl) * pad_frac
    top_p, bot_p = ph + pad, pl - pad
    span = top_p - bot_p
    if span <= 0:
        return 0.5
    return float((top_p - price) / span)


def trade_levels_to_overlay_fracs(
    entry: float,
    sl: float,
    target: float,
    df: Any,
) -> dict[str, float]:
    """Convert swing-based prices to PIL y-fractions for `_draw_screenshot_overlays`."""
    ph = float(df["High"].max())
    pl = float(df["Low"].min())
    return {
        "entry_frac": _y_frac_for_price(entry, ph, pl),
        "sl_frac": _y_frac_for_price(sl, ph, pl),
        "target_frac": _y_frac_for_price(target, ph, pl),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE LEVEL DRAWER — price-tag style labels
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_level(
    ax,
    price: float,
    label: str,
    colour: str,
    ls: str = "--",
    *,
    fontsize: float = 7.5,
    lw: float = 1.4,
) -> None:
    """
    Draw a horizontal price level with a floating tag box on the right axis.
    Dotted/dashed line + coloured filled box with white label text.
    """
    xmin, xmax = ax.get_xlim()
    span = xmax - xmin

    # Dotted line across the full chart width
    ax.axhline(price, color=colour, lw=lw, ls=ls, alpha=0.85, zorder=4)

    # Tag box — sits just past the right edge of the data, clipped by axis
    tag_x = xmax - span * 0.002
    tag_txt = f" {label}  {price:,.0f} "

    ax.text(
        tag_x, price, tag_txt,
        color="white",
        fontsize=fontsize,
        fontweight="bold",
        va="center",
        ha="right",
        zorder=6,
        bbox=dict(
            boxstyle=f"round,pad={0.15 if fontsize < 7 else 0.25}",
            facecolor=colour,
            edgecolor="none",
            alpha=0.90,
        ),
    )


def _cheatsheet_trade_prices(df, strategy_id: str) -> tuple[float, float, float]:
    """Return (entry, sl, target) from swing structure (same as main chart)."""
    return trade_levels_from_swings(df, strategy_id)


def _add_cheatsheet_trade_levels_and_markers(ax, df, pal: dict, strategy_id: str) -> None:
    """
    On the small cheatsheet strip: horizontal ENTRY / SL / TARGET plus markers
    on the time axis (entry candle, SL level, target hit).
    """
    entry, sl, target = _cheatsheet_trade_prices(df, strategy_id)
    fs = 5.0
    lw = 1.0

    _draw_level(ax, target, "TARGET", pal["target"], ls="--", fontsize=fs, lw=lw)
    _draw_level(ax, entry,  "ENTRY",  pal["entry"],  ls="-",  fontsize=fs, lw=lw)
    _draw_level(ax, sl,     "SL",     pal["sl"],     ls="--", fontsize=fs, lw=lw)

    xmin, xmax = ax.get_xlim()
    span = xmax - xmin
    # Left → right story: entry (zone) → SL reference → target hit (later)
    x_entry = xmin + span * 0.52
    x_sl = xmin + span * 0.48
    x_tgt = xmin + span * 0.82

    meta = _STRATEGY_META.get(strategy_id, {"bias": "BULLISH"})
    bias = meta["bias"]

    # Entry candle marker (triangle at entry price)
    ax.scatter(
        [x_entry], [entry],
        s=55, c=pal["entry"], marker="^" if bias == "BULLISH" else "v",
        zorder=12, edgecolors="white", linewidths=0.6,
    )
    ax.text(
        x_entry, entry,
        "  ENTRY",
        fontsize=fs, color=pal["entry"], fontweight="bold",
        ha="left", va="bottom" if bias == "BULLISH" else "top",
    )

    # SL: marker at SL price (stop sits beyond invalidation)
    ax.scatter(
        [x_sl], [sl],
        s=45, c=pal["sl"], marker="v" if bias == "BULLISH" else "^",
        zorder=12, edgecolors="white", linewidths=0.5,
    )
    ax.text(
        x_sl, sl,
        "  SL",
        fontsize=fs, color=pal["sl"], fontweight="bold",
        ha="right", va="top" if bias == "BULLISH" else "bottom",
    )

    # Target hit (star at target)
    ax.scatter(
        [x_tgt], [target],
        s=70, c=pal["target"], marker="*",
        zorder=12, edgecolors="white", linewidths=0.5,
    )
    ax.text(
        x_tgt, target,
        "  TGT",
        fontsize=fs, color=pal["target"], fontweight="bold",
        ha="left", va="bottom" if bias == "BULLISH" else "top",
    )

    # Thin flow hint: entry → target (bullish: up)
    ax.annotate(
        "",
        xy=(x_tgt, target), xytext=(x_entry, entry),
        arrowprops=dict(
            arrowstyle="-|>", color=pal["muted"], lw=0.8,
            alpha=0.45, shrinkA=6, shrinkB=6,
        ),
        zorder=5,
    )


def _add_trade_levels(ax, df, pal: dict, strategy_id: str) -> None:
    """ENTRY / SL / TARGET from recent swings + liquidity (see ``trade_levels_from_swings``)."""
    entry, sl, target = trade_levels_from_swings(df, strategy_id)

    _draw_level(ax, target, "TARGET", pal["target"], ls="--")
    _draw_level(ax, entry,  "ENTRY",  pal["entry"],  ls="-")
    _draw_level(ax, sl,     "SL",     pal["sl"],     ls="--")


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY OVERLAY BUILDERS
# Each returns (add_plots_list, annotate_fn)
# annotate_fn(ax, df, pal) draws directly onto the matplotlib axes
# ═══════════════════════════════════════════════════════════════════════════════

def _overlay_order_block(df, pal):
    highs  = df["High"].values
    lows   = df["Low"].values
    closes = df["Close"].values
    n      = len(df)

    # Find last bearish candle before a bullish impulse (genuine OB logic)
    ob_idx = None
    for i in range(n - 4, max(5, n - 20), -1):
        if closes[i] < closes[i - 1]:          # bearish candle
            if closes[i + 1] > closes[i + 1 - 1]:   # followed by bullish
                ob_idx = i
                break

    if ob_idx is not None:
        ob_lo = float(lows[ob_idx])
        ob_hi = float(highs[ob_idx])
    else:
        # Fallback: tight 4-candle window around mid-chart low cluster
        mid   = max(0, n - 14)
        ob_lo = float(lows[mid:mid + 6].min())
        ob_hi = float(highs[mid:mid + 6].min() * 1.002)

    # Never let zone span more than 0.4% of price (keep it tight)
    if ob_hi - ob_lo > ob_lo * 0.004:
        ob_hi = ob_lo + ob_lo * 0.003

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        # Glow fill
        ax.axhspan(ob_lo, ob_hi, xmin=0.45, xmax=0.88,
                   color=p["yellow"], alpha=0.10, zorder=2)
        ax.axhspan(ob_lo, ob_hi, xmin=0.45, xmax=0.88,
                   color=p["yellow"], alpha=0.06, zorder=2)
        # Borders
        ax.axhline(ob_hi, color=p["yellow"], lw=1.2, ls="--", alpha=0.75, zorder=3)
        ax.axhline(ob_lo, color=p["yellow"], lw=1.2, ls="--", alpha=0.75, zorder=3)
        mid = (ob_hi + ob_lo) / 2
        ax.text(xmin + span * 0.48, mid, "ORDER BLOCK",
                color=p["yellow"], fontsize=7.5, fontweight="bold",
                va="center", zorder=5,
                bbox=dict(boxstyle="round,pad=0.2", fc=p["yellow"], alpha=0.12, ec="none"))
        # Retest arrow
        ax.annotate("",
                    xy=(xmax - span * 0.06, ob_hi + (ob_hi - ob_lo) * 0.2),
                    xytext=(xmax - span * 0.06, ob_hi + (ob_hi - ob_lo) * 1.6),
                    arrowprops=dict(arrowstyle="-|>", color=p["entry"], lw=2.0), zorder=6)
    return [], annotate


def _overlay_fvg(df, pal):
    highs = df["High"].values
    lows  = df["Low"].values
    n     = len(df)
    fvg_lo, fvg_hi = None, None
    for i in range(n - 3, max(0, n - 22), -1):
        lo = highs[i - 1] if i > 0 else lows[i]
        hi = lows[i + 1]  if i < n - 1 else highs[i]
        if hi > lo * 1.0008:
            fvg_lo, fvg_hi = float(lo), float(hi)
            break
    if fvg_lo is None:
        fvg_lo  = float(lows[-12:].min())
        fvg_hi  = fvg_lo * 1.003

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        ax.axhspan(fvg_lo, fvg_hi, xmin=0.42, xmax=0.90,
                   color=p["purple"], alpha=0.18, zorder=2)
        ax.axhline(fvg_hi, color=p["purple"], lw=0.8, ls=":", alpha=0.70)
        ax.axhline(fvg_lo, color=p["purple"], lw=0.8, ls=":", alpha=0.70)
        ax.text(xmin + span * 0.44, (fvg_hi + fvg_lo) / 2, "FVG / IMBALANCE",
                color=p["purple"], fontsize=7.5, fontweight="bold", va="center", zorder=5,
                bbox=dict(boxstyle="round,pad=0.2", fc=p["purple"], alpha=0.12, ec="none"))
    return [], annotate


def _overlay_vwap(df, pal):
    vwap = _calc_vwap(df)
    plots = [mpf.make_addplot(vwap, color=pal["accent"], width=2.0, linestyle="-", alpha=0.9)]

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        v_last = float(vwap.iloc[-1])
        ax.text(xmax - span * 0.01, v_last, " VWAP",
                color=p["accent"], fontsize=7.5, fontweight="bold", va="center",
                bbox=dict(boxstyle="round,pad=0.2", fc=p["accent"], alpha=0.15, ec="none"))
    return plots, annotate


def _overlay_ema_pullback(df, pal):
    ema = _calc_ema(df, 21)
    plots = [mpf.make_addplot(ema, color=pal["ema"], width=1.8, linestyle="-", alpha=0.9)]

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        e_last = float(ema.iloc[-1])
        ax.text(xmax - span * 0.01, e_last, " EMA 21",
                color=p["ema"], fontsize=7.5, fontweight="bold", va="center",
                bbox=dict(boxstyle="round,pad=0.2", fc=p["ema"], alpha=0.15, ec="none"))
        ax.annotate("PULLBACK\nENTRY ZONE",
                    xy=(xmax - span * 0.09, e_last),
                    xytext=(xmax - span * 0.30, e_last * 0.9975),
                    color=p["entry"], fontsize=7.5, fontweight="bold",
                    arrowprops=dict(arrowstyle="-|>", color=p["entry"], lw=1.8), zorder=6)
    return plots, annotate


def _overlay_orb(df, pal):
    orb_hi = float(df["High"].iloc[:3].max())
    orb_lo = float(df["Low"].iloc[:3].min())

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        ax.axhspan(orb_lo, orb_hi, xmin=0.0, xmax=0.10,
                   color=p["accent"], alpha=0.22, zorder=2)
        ax.axhline(orb_hi, color=p["entry"], lw=1.4, ls="--", alpha=0.85, zorder=3)
        ax.axhline(orb_lo, color=p["sl"],    lw=1.4, ls="--", alpha=0.85, zorder=3)
        ax.text(xmin + span * 0.01, orb_hi * 1.0008, "ORB HIGH",
                color=p["entry"], fontsize=7, fontweight="bold")
        ax.text(xmin + span * 0.01, orb_lo * 0.9992, "ORB LOW",
                color=p["sl"],    fontsize=7, fontweight="bold")
        ax.annotate("",
                    xy=(xmax - span * 0.05, orb_hi * 1.004),
                    xytext=(xmax - span * 0.30, orb_hi * 1.0015),
                    arrowprops=dict(arrowstyle="-|>", color=p["entry"], lw=2.2), zorder=6)
    return [], annotate


def _overlay_range_breakout(df, pal):
    resistance = float(df["High"].iloc[:-4].quantile(0.92))

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        ax.axhline(resistance, color=p["sl"], lw=1.8, ls="-", alpha=0.80, zorder=3)
        ax.axhspan(resistance * 0.9985, resistance * 1.0015,
                   color=p["sl"], alpha=0.07, zorder=2)
        ax.text(xmin + span * 0.02, resistance * 1.001, "RESISTANCE",
                color=p["sl"], fontsize=7.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc=p["sl"], alpha=0.12, ec="none"))
        ax.annotate("BREAKOUT ↑",
                    xy=(xmax - span * 0.05, resistance * 1.005),
                    xytext=(xmax - span * 0.28, resistance * 1.010),
                    color=p["entry"], fontsize=8, fontweight="bold",
                    arrowprops=dict(arrowstyle="-|>", color=p["entry"], lw=2.2), zorder=6)
    return [], annotate


def _overlay_sr_flip(df, pal):
    support = float(df["Low"].iloc[:-4].quantile(0.14))

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        ax.axhline(support, color=p["sl"], lw=1.6, ls="--", alpha=0.85, zorder=3)
        ax.axhspan(support * 0.9990, support * 1.0010,
                   color=p["sl"], alpha=0.07, zorder=2)
        ax.text(xmin + span * 0.50, support * 1.001, "S → R FLIP",
                color=p["sl"], fontsize=7.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc=p["sl"], alpha=0.12, ec="none"))
        ax.annotate("",
                    xy=(xmax - span * 0.10, support * 1.006),
                    xytext=(xmax - span * 0.10, support * 0.994),
                    arrowprops=dict(arrowstyle="-|>", color=p["sl"], lw=2.0), zorder=6)
    return [], annotate


def _overlay_trendline(df, pal):
    lows = df["Low"].values
    n    = len(lows)
    i1, i2 = int(n * 0.18), int(n * 0.58)
    x1, y1 = i1, float(lows[i1])
    x2, y2 = i2, float(lows[i2])

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        total = len(df)
        mx1  = xmin + (xmax - xmin) * (x1 / total)
        mx2  = xmin + (xmax - xmin) * (x2 / total)
        mxe  = xmax
        slope = (y2 - y1) / max(mx2 - mx1, 1e-9)
        yend  = y2 + slope * (mxe - mx2)
        ax.plot([mx1, mxe], [y1, yend], color=p["ema"], lw=1.8, ls="-", alpha=0.88, zorder=3)
        ax.text(mx2, y2 * 0.9978, "TRENDLINE", color=p["ema"],
                fontsize=7, fontweight="bold", va="top")
        bx  = xmin + (xmax - xmin) * 0.80
        by  = y2 + slope * (bx - mx2)
        ax.annotate("BREAK",
                    xy=(bx, by * 0.9978),
                    xytext=(bx, by * 0.9915),
                    color=p["sl"], fontsize=8, fontweight="bold", ha="center",
                    arrowprops=dict(arrowstyle="-|>", color=p["sl"], lw=1.8), zorder=6)
    return [], annotate


def _overlay_consolidation(df, pal):
    mid      = len(df) // 2
    box_hi   = float(df["High"].iloc[mid:mid+10].max())
    box_lo   = float(df["Low"].iloc[mid:mid+10].min())

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        xs   = xmin + span * 0.38
        xe   = xmin + span * 0.58
        rect = mpatches.FancyBboxPatch(
            (xs, box_lo), xe - xs, box_hi - box_lo,
            boxstyle="square,pad=0", linewidth=1.4,
            edgecolor=p["accent"], facecolor=p["accent"], alpha=0.10, zorder=2)
        ax.add_patch(rect)
        ax.text((xs + xe) / 2, box_hi * 1.001, "CONSOLIDATION",
                color=p["accent"], fontsize=7, fontweight="bold", ha="center", zorder=5)
        ax.annotate("",
                    xy=(xmax - span * 0.06, box_hi * 1.005),
                    xytext=(xe, box_hi),
                    arrowprops=dict(arrowstyle="-|>", color=p["entry"], lw=2.2), zorder=6)
    return [], annotate


def _overlay_momentum_scalp(df, pal):
    n = len(df)
    bs = max(0, n - 7)

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        blo = float(df["Low"].iloc[bs:].min())
        bhi = float(df["High"].iloc[bs:].max())
        ax.axhspan(blo, bhi, xmin=0.82, xmax=1.0, color=p["entry"], alpha=0.10, zorder=2)
        ax.text(xmax - span * 0.16, bhi * 1.001, "MOMENTUM BURST",
                color=p["entry"], fontsize=7.5, fontweight="bold", zorder=5)
    return [], annotate


def _overlay_liquidity_grab(df, pal):
    eq_hi = float(df["High"].iloc[-18:-4].quantile(0.94))

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        ax.axhline(eq_hi, color=p["sl"], lw=1.4, ls=":", alpha=0.90, zorder=3)
        ax.axhspan(eq_hi * 0.9992, eq_hi * 1.0008, color=p["sl"], alpha=0.07, zorder=2)
        ax.text(xmin + span * 0.04, eq_hi * 1.001, "EQUAL HIGHS  (LIQUIDITY POOL)",
                color=p["sl"], fontsize=7.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc=p["sl"], alpha=0.10, ec="none"))
        ax.annotate("SWEEP\n& REVERSE",
                    xy=(xmax - span * 0.14, eq_hi * 1.002),
                    xytext=(xmax - span * 0.36, eq_hi * 1.008),
                    color=p["accent2"], fontsize=7.5, fontweight="bold",
                    arrowprops=dict(arrowstyle="-|>", color=p["accent2"], lw=1.8), zorder=6)
    return [], annotate


def _overlay_breaker_block(df, pal):
    mid   = len(df) // 3
    bb_hi = float(df["High"].iloc[mid:mid+7].max())
    bb_lo = float(df["Low"].iloc[mid:mid+7].min())

    def annotate(ax, df, p):
        xmin, xmax = ax.get_xlim()
        span = xmax - xmin
        ax.axhspan(bb_lo, bb_hi, xmin=0.18, xmax=0.44,
                   color=p["sl"], alpha=0.12, zorder=2)
        ax.axhline(bb_hi, color=p["sl"], lw=1.0, ls=":", alpha=0.70)
        ax.axhline(bb_lo, color=p["sl"], lw=1.0, ls=":", alpha=0.70)
        ax.text(xmin + span * 0.19, bb_hi * 1.001, "BREAKER BLOCK",
                color=p["sl"], fontsize=7.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", fc=p["sl"], alpha=0.10, ec="none"))
    return [], annotate


_OVERLAY_MAP = {
    "order_block":            _overlay_order_block,
    "fvg":                    _overlay_fvg,
    "vwap_strategy":          _overlay_vwap,
    "trend_pullback":         _overlay_ema_pullback,
    "orb":                    _overlay_orb,
    "range_breakout":         _overlay_range_breakout,
    "sr_flip":                _overlay_sr_flip,
    "trendline_break":        _overlay_trendline,
    "consolidation_breakout": _overlay_consolidation,
    "momentum_scalp":         _overlay_momentum_scalp,
    "liquidity_grab":         _overlay_liquidity_grab,
    "breaker_block":          _overlay_breaker_block,
}


# ═══════════════════════════════════════════════════════════════════════════════
# WATERMARK
# ═══════════════════════════════════════════════════════════════════════════════

_LOGO_PATH = Path(__file__).parent.parent / "assets" / "logo.png"
_LOGO_IMG: Image.Image | None = None


def _get_logo() -> Image.Image | None:
    """Load and cache the logo PNG; return None if not found."""
    global _LOGO_IMG
    if _LOGO_IMG is not None:
        return _LOGO_IMG
    if _LOGO_PATH.exists():
        try:
            img = Image.open(_LOGO_PATH).convert("RGBA")
            _LOGO_IMG = img
            return _LOGO_IMG
        except Exception:
            pass
    return None


def _add_watermark(ax, text: str = "StocksWithGaurav") -> None:
    """Faint centred logo watermark on the chart axes (falls back to text)."""
    logo = _get_logo()
    if logo is not None:
        # We paste the logo onto the axes via AnnotationBbox after saving,
        # so here we just add a very faint text fallback at minimum opacity
        # so the axis area is not completely empty.
        ax.text(
            0.5, 0.5, "",
            transform=ax.transAxes, fontsize=1, color="white", alpha=0.0,
            ha="center", va="center", zorder=1,
        )
    else:
        ax.text(
            0.5, 0.5, text,
            transform=ax.transAxes, fontsize=28, color="white", alpha=0.04,
            ha="center", va="center", fontweight="bold", rotation=0, zorder=1,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE CONTEXT BOX — "SETUP / BIAS / TF" card (top-left of chart)
# ═══════════════════════════════════════════════════════════════════════════════

# Per-strategy bias metadata
_STRATEGY_META = {
    "order_block":            {"bias": "BULLISH",  "label": "ORDER BLOCK"},
    "fvg":                    {"bias": "BULLISH",  "label": "FAIR VALUE GAP"},
    "vwap_strategy":          {"bias": "BULLISH",  "label": "VWAP RECLAIM"},
    "trend_pullback":         {"bias": "BULLISH",  "label": "TREND PULLBACK"},
    "orb":                    {"bias": "BULLISH",  "label": "ORB BREAKOUT"},
    "range_breakout":         {"bias": "BULLISH",  "label": "RANGE BREAKOUT"},
    "sr_flip":                {"bias": "BEARISH",  "label": "S/R FLIP"},
    "trendline_break":        {"bias": "BEARISH",  "label": "TRENDLINE BREAK"},
    "consolidation_breakout": {"bias": "BULLISH",  "label": "CONSOLIDATION BO"},
    "momentum_scalp":         {"bias": "BULLISH",  "label": "MOMENTUM SCALP"},
    "liquidity_grab":         {"bias": "BEARISH",  "label": "LIQUIDITY GRAB"},
    "breaker_block":          {"bias": "BEARISH",  "label": "BREAKER BLOCK"},
}

# Entry confirmation labels per strategy (randomised slightly each run)
_CONFIRM_LABELS = {
    "order_block":            ["RETEST", "REJECTION"],
    "fvg":                    ["FILL & CONTINUE", "RETEST"],
    "vwap_strategy":          ["RECLAIM", "CONFIRMATION"],
    "trend_pullback":         ["BOUNCE", "CONFIRMATION"],
    "orb":                    ["BREAK", "BREAKOUT"],
    "range_breakout":         ["BREAKOUT", "BREAK"],
    "sr_flip":                ["REJECTION", "RETEST"],
    "trendline_break":        ["BREAK", "CONFIRMATION"],
    "consolidation_breakout": ["BREAKOUT", "CONTINUATION"],
    "momentum_scalp":         ["BURST", "ENTRY"],
    "liquidity_grab":         ["SWEEP", "REVERSAL"],
    "breaker_block":          ["FLIP", "REJECTION"],
}

# Confidence per strategy (based on smc_score from strategy_agent)
_STRATEGY_CONFIDENCE = {
    "order_block":            "HIGH",
    "fvg":                    "HIGH",
    "vwap_strategy":          "HIGH",
    "trend_pullback":         "HIGH",
    "orb":                    "MEDIUM",
    "range_breakout":         "MEDIUM",
    "sr_flip":                "HIGH",
    "trendline_break":        "MEDIUM",
    "consolidation_breakout": "MEDIUM",
    "momentum_scalp":         "LOW",
    "liquidity_grab":         "HIGH",
    "breaker_block":          "HIGH",
}

_CONFIDENCE_COLOUR = {
    "HIGH":   "#00c853",   # green
    "MEDIUM": "#ff9800",   # orange
    "LOW":    "#ff1744",   # red
}


def _add_context_box(ax, strategy_id: str, pal: dict) -> None:
    """
    Draw a small trade-context info card in the top-left of the chart axes.

    Contains:
        SETUP: <strategy label>
        BIAS:  BULLISH / BEARISH
        TF:    5M
    """
    meta  = _STRATEGY_META.get(strategy_id, {"bias": "BULLISH", "label": strategy_id.upper()})
    bias  = meta["bias"]
    label = meta["label"]

    bias_colour = pal["entry"] if bias == "BULLISH" else pal["sl"]

    lines = [
        ("SETUP", label,   "white"),
        ("BIAS",  bias,    bias_colour),
        ("TF",    "5M",    pal["muted"]),
    ]

    # Build multiline text as a single annotation with a dark background box
    content = "\n".join(f"{k}: {v}" for k, v, _ in lines)

    ax.text(
        0.012, 0.97, content,
        transform  = ax.transAxes,
        fontsize   = 7.8,
        color      = "white",
        va         = "top",
        ha         = "left",
        fontfamily = "monospace",
        zorder     = 8,
        linespacing= 1.6,
        bbox       = dict(
            boxstyle    = "round,pad=0.45",
            facecolor   = "#0b0f17",
            edgecolor   = "#2a3550",
            alpha       = 0.88,
            linewidth   = 0.8,
        ),
    )

    # Colour the BIAS value by overlaying a second text on the same spot
    # (matplotlib doesn't support per-word colour in a single text call)
    bias_line_idx = 1   # 0=SETUP, 1=BIAS, 2=TF
    bias_y = 0.97 - (bias_line_idx * 0.042)   # rough line offset in axes coords
    ax.text(
        0.012 + 0.055, bias_y,   # nudge past "BIAS: "
        bias,
        transform  = ax.transAxes,
        fontsize   = 7.8,
        color      = bias_colour,
        va         = "top",
        ha         = "left",
        fontfamily = "monospace",
        fontweight = "bold",
        zorder     = 9,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY CONFIRMATION ARROW + LABEL
# ═══════════════════════════════════════════════════════════════════════════════

def _add_entry_confirmation(ax, df, strategy_id: str, pal: dict) -> None:
    """
    Draw a directional arrow at the entry candle with a confirmation label.

    Arrow points upward (bullish) or downward (bearish) depending on bias.
    Positioned at the last candle to simulate "entry here" on the live chart.
    """
    meta  = _STRATEGY_META.get(strategy_id, {"bias": "BULLISH", "label": ""})
    bias  = meta["bias"]

    confirm_pool = _CONFIRM_LABELS.get(strategy_id, ["CONFIRMATION"])
    confirm_text = random.choice(confirm_pool)

    entry_px, _sl_px, _tgt = trade_levels_from_swings(df, strategy_id)
    spread = float(df["High"].max() - df["Low"].min())
    if spread <= 0:
        spread = float(df["Close"].iloc[-1]) * 0.001
    xmin, xmax = ax.get_xlim()
    span = xmax - xmin

    # Entry x — near the right end of the chart (last candle area)
    entry_x = xmax - span * 0.06

    if bias == "BULLISH":
        arrow_tail_y = entry_px - spread * 0.06   # below entry
        arrow_head_y = entry_px - spread * 0.01   # pointing up into entry
        label_y      = arrow_tail_y - spread * 0.01
        colour       = pal["entry"]
        va_label     = "top"
    else:
        arrow_tail_y = entry_px + spread * 0.06   # above entry
        arrow_head_y = entry_px + spread * 0.01   # pointing down into entry
        label_y      = arrow_tail_y + spread * 0.01
        colour       = pal["sl"]
        va_label     = "bottom"

    ax.annotate(
        "",
        xy     = (entry_x, arrow_head_y),
        xytext = (entry_x, arrow_tail_y),
        arrowprops = dict(
            arrowstyle = "-|>",
            color      = colour,
            lw         = 2.5,
        ),
        zorder = 9,
    )

    ax.text(
        entry_x, label_y,
        f">> {confirm_text}",
        color      = colour,
        fontsize   = 7.5,
        fontweight = "bold",
        ha         = "center",
        va         = va_label,
        zorder     = 9,
        bbox       = dict(
            boxstyle  = "round,pad=0.22",
            facecolor = colour,
            edgecolor = "none",
            alpha     = 0.15,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE FLOW ARROW — ENTRY → TARGET subtle direction line
# ═══════════════════════════════════════════════════════════════════════════════

def _add_trade_flow(ax, df, strategy_id: str, pal: dict) -> None:
    """
    Draw a subtle semi-transparent arrow from the entry level toward the target,
    giving the viewer an immediate sense of trade direction and magnitude.
    """
    meta = _STRATEGY_META.get(strategy_id, {"bias": "BULLISH"})
    bias = meta["bias"]

    entry, _sl, target = trade_levels_from_swings(df, strategy_id)

    xmin, xmax = ax.get_xlim()
    span = xmax - xmin

    # Arrow spans from 65% → 90% of chart width horizontally
    x_start = xmin + span * 0.65
    x_end   = xmin + span * 0.90

    colour = pal["entry"] if bias == "BULLISH" else pal["sl"]

    ax.annotate(
        "",
        xy     = (x_end,   target),
        xytext = (x_start, entry),
        arrowprops = dict(
            arrowstyle      = "-|>",
            color           = colour,
            lw              = 1.2,
            connectionstyle = "arc3,rad=0.0",
        ),
        alpha  = 0.45,
        zorder = 3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE BADGE — top-right corner of chart
# ═══════════════════════════════════════════════════════════════════════════════

def _add_confidence_badge(ax, strategy_id: str) -> None:
    """
    Draw a small HIGH / MEDIUM / LOW confidence badge in the top-right corner.
    Colour-coded: green = HIGH, orange = MEDIUM, red = LOW.
    """
    level  = _STRATEGY_CONFIDENCE.get(strategy_id, "MEDIUM")
    colour = _CONFIDENCE_COLOUR[level]
    label  = f"* {level} CONF"

    ax.text(
        0.988, 0.97,
        label,
        transform  = ax.transAxes,
        fontsize   = 7.5,
        color      = colour,
        va         = "top",
        ha         = "right",
        fontweight = "bold",
        zorder     = 8,
        bbox       = dict(
            boxstyle  = "round,pad=0.35",
            facecolor = "#0b0f17",
            edgecolor = colour,
            alpha     = 0.85,
            linewidth = 0.9,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CHART RENDERER (mplfinance + annotations)
# ═══════════════════════════════════════════════════════════════════════════════


def _strip_datetime_axis_labels(axes: Any) -> None:
    """
    Remove x-axis date/time tick labels from all mplfinance panels (price + volume).
    Keeps chart posts timeless (no session timestamps on the candle or volume row).
    """
    try:
        if isinstance(axes, np.ndarray):
            ax_list = axes.ravel().tolist()
        elif isinstance(axes, (list, tuple)):
            ax_list = list(axes)
        else:
            ax_list = [axes]
    except Exception:
        ax_list = [axes]

    for ax in ax_list:
        try:
            ax.set_xlabel("")
            ax.tick_params(axis="x", which="both", labelbottom=False)
            for lbl in ax.get_xticklabels():
                lbl.set_visible(False)
        except Exception:
            continue


def _render_chart_with_overlay(
    df: Any,
    strategy_id: str,
    pal: dict,
    symbol: str,
    *,
    out_w: int = CANVAS_W,
    out_h: int = CHART_H,
    cheatsheet_mode: bool = False,
) -> Image.Image:
    """
    Render candlesticks + strategy overlays into a PIL Image.

    cheatsheet_mode:
        Compact strip for cheatsheet embed: no volume, no trade UI clutter,
        only SMC zone overlays + faint optional logo. Looks like a TradingView
        screenshot (same data/style as full posts — Yahoo 5m NIFTY).
    """
    dpi   = 108 if out_w >= 800 else max(72, min(110, out_w // 9))
    fig_w = out_w / dpi
    fig_h = out_h / dpi

    tick_sz = 7.5 if out_h >= 400 else 5.0

    mc = mpf.make_marketcolors(
        up     = pal["candle_up"],
        down   = pal["candle_dn"],
        wick   = {"up": pal["wick"], "down": pal["wick"]},
        edge   = {"up": pal["candle_up"], "down": pal["candle_dn"]},
        volume = {"up": pal["candle_up"] + "aa", "down": pal["candle_dn"] + "aa"},
    )
    style = mpf.make_mpf_style(
        marketcolors = mc,
        facecolor    = pal["chart_bg"],
        edgecolor    = pal["border"],
        figcolor     = pal["bg"],
        gridcolor    = pal["grid"],
        gridstyle    = "-",
        gridaxis     = "both",
        rc = {
            "axes.labelcolor":       pal["muted"],
            "xtick.color":           pal["muted"],
            "ytick.color":           pal["fg"],
            "xtick.labelsize":       tick_sz,
            "ytick.labelsize":       tick_sz,
            "xtick.major.pad":       2 if cheatsheet_mode else 4,
            "ytick.major.pad":       2 if cheatsheet_mode else 4,
            "axes.spines.top":       False,
            "axes.spines.right":     False,
            "grid.alpha":            0.25,
            "grid.linewidth":        0.5,
        },
    )

    # Get overlay add_plots + annotation function
    overlay_builder = _OVERLAY_MAP.get(strategy_id)
    add_plots, annotate_fn = [], None
    if overlay_builder:
        add_plots, annotate_fn = overlay_builder(df, pal)

    show_volume = _has_real_volume(df) and not cheatsheet_mode

    pad = dict(left=0.05, top=0.08, right=0.12, bottom=0.08) if cheatsheet_mode else dict(left=0.06, top=0.10, right=0.14, bottom=0.06)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        plot_kwargs = dict(
            type               = "candle",
            style              = style,
            figsize            = (fig_w, fig_h),
            tight_layout       = False,
            addplot            = add_plots,
            warn_too_much_data = 200,
            returnfig          = True,
            scale_padding      = pad,
        )
        if show_volume:
            plot_kwargs.update(volume=True, volume_panel=1, panel_ratios=(5, 1))

        fig, axes = mpf.plot(df, **plot_kwargs)

        # Timeless chart: no date/time on x-axis (main or volume panel)
        _strip_datetime_axis_labels(axes)

        ax_main = axes[0]   # price axis always first

        # ── Strategy annotation (zones, lines, FVG, etc.) ───────────────────────
        if annotate_fn:
            annotate_fn(ax_main, df, pal)

        if not cheatsheet_mode:
            # ── ENTRY / SL / TARGET price-tag levels ──────────────────────────
            _add_trade_levels(ax_main, df, pal, strategy_id)

            # ── Trade flow arrow (ENTRY → TARGET direction) ───────────────────
            _add_trade_flow(ax_main, df, strategy_id, pal)

            # ── Entry confirmation arrow + label ─────────────────────────────
            _add_entry_confirmation(ax_main, df, strategy_id, pal)

            # ── Trade context box (top-left) ─────────────────────────────────
            _add_context_box(ax_main, strategy_id, pal)

            # ── Confidence badge (top-right) ─────────────────────────────────
            _add_confidence_badge(ax_main, strategy_id)

            # ── Watermark ─────────────────────────────────────────────────────
            _add_watermark(ax_main)
        else:
            # Cheatsheet strip: ENTRY / SL / TARGET lines + candle markers + caption
            _add_cheatsheet_trade_levels_and_markers(ax_main, df, pal, strategy_id)
            sym_clean = chart_display_symbol(symbol)
            ax_main.text(
                0.01, 0.02,
                f"{sym_clean}  ·  5m",
                transform=ax_main.transAxes,
                fontsize=5.5,
                color=pal["muted"],
                va="bottom",
                ha="left",
            )

        # ── Volume panel styling ─────────────────────────────────────────────────
        if show_volume and len(axes) >= 3:
            ax_vol = axes[2]
            ax_vol.set_facecolor(pal["panel_bg"])
            for spine in ax_vol.spines.values():
                spine.set_edgecolor(pal["border"])

        # Overall figure background
        fig.patch.set_facecolor(pal["bg"])

        fig.savefig(
            tmp_path,
            dpi         = dpi,
            bbox_inches = "tight",
            pad_inches  = 0,
            facecolor   = pal["bg"],
        )
        plt.close(fig)

        chart_img = Image.open(tmp_path).convert("RGBA")
        chart_img = chart_img.resize((out_w, out_h), Image.LANCZOS)

        # ── Logo watermark (centre, very faint) — skip on cheatsheet (page has logo)
        logo = _get_logo()
        if logo is not None and not cheatsheet_mode:
            logo_w = int(out_w * 0.30)
            logo_h = int(logo.height * logo_w / logo.width)
            logo_r = logo.resize((logo_w, logo_h), Image.LANCZOS)
            r, g, b, a = logo_r.split()
            a = a.point(lambda x: int(x * 0.08))
            logo_faint = Image.merge("RGBA", (r, g, b, a))
            paste_x = (out_w - logo_w) // 2
            paste_y = (out_h - logo_h) // 2
            chart_img.paste(logo_faint, (paste_x, paste_y), logo_faint)

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return chart_img


# ═══════════════════════════════════════════════════════════════════════════════
# PIL COMPOSITOR — assembles header + chart + footer into 1080×1080
# ═══════════════════════════════════════════════════════════════════════════════

def _text_size(draw: ImageDraw.Draw, text: str, font) -> tuple[int, int]:
    try:
        _, _, w, h = draw.textbbox((0, 0), text, font=font)
        return w, h
    except Exception:
        return len(text) * 14, 24


def _word_wrap(draw: ImageDraw.Draw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if _text_size(draw, test, font)[0] > max_w:
            if cur:
                lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _composite(
    chart_img: Image.Image,
    strategy: dict,
    pal: dict,
    symbol: str,
) -> Image.Image:
    bg_rgb     = _hex_to_rgb(pal["bg"])
    hdr_rgb    = _hex_to_rgb(pal["header_bg"])
    ftr_rgb    = _hex_to_rgb(pal["footer_bg"])
    fg_rgb     = _hex_to_rgb(pal["fg"])
    acc_rgb    = _hex_to_rgb(pal["accent"])
    muted_rgb  = _hex_to_rgb(pal["muted"])
    entry_rgb  = _hex_to_rgb(pal["entry"])
    sl_rgb     = _hex_to_rgb(pal["sl"])
    target_rgb = _hex_to_rgb(pal["target"])
    div_rgb    = _hex_to_rgb(pal["divider"])

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), bg_rgb)
    draw   = ImageDraw.Draw(canvas)

    # ── HEADER ────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, CANVAS_W, HEADER_H], fill=hdr_rgb)

    # Thin accent bar at very top
    draw.rectangle([0, 0, CANVAS_W, 3], fill=_hex_to_rgb(pal["accent"]))

    hook  = random.choice(_HOOKS)
    title = strategy.get("name", strategy.get("id", "SETUP")).upper()

    f_hook  = _font("regular", 26)
    f_title = _font("bold", 50)

    hw, hh = _text_size(draw, hook, f_hook)
    draw.text(((CANVAS_W - hw) // 2, 12), hook, font=f_hook, fill=(180, 195, 230))

    # Auto-shrink title to fit
    ty = 12 + hh + 8
    for sz in (50, 44, 38, 32):
        f_title = _font("bold", sz)
        tw, th = _text_size(draw, title, f_title)
        if tw <= CANVAS_W - 80:
            break
    draw.text(((CANVAS_W - tw) // 2, ty), title, font=f_title, fill=(255, 255, 255))

    # ── CHART ─────────────────────────────────────────────────────────────────
    canvas.paste(chart_img.convert("RGB"), (0, HEADER_H))

    # ── FOOTER ────────────────────────────────────────────────────────────────
    fy0 = HEADER_H + CHART_H
    draw.rectangle([0, fy0, CANVAS_W, CANVAS_H], fill=ftr_rgb)

    # Subtle top divider
    draw.rectangle([0, fy0, CANVAS_W, fy0 + 1], fill=div_rgb)

    # Concept line (1–2 lines, 27px)
    concept = strategy.get("concept", "")
    if concept:
        f_con = _font("regular", 26)
        lines = _word_wrap(draw, concept, f_con, CANVAS_W - 80)
        cy = fy0 + 14
        for line in lines[:2]:
            draw.text((40, cy), line, font=f_con, fill=fg_rgb)
            _, lh = _text_size(draw, line, f_con)
            cy += lh + 5

    # Human-touch closer (bold accent, no emoji — PIL on Windows lacks emoji font)
    closer = random.choice(_CLOSERS)
    f_cl = _font("bold", 25)
    draw.text((40, fy0 + 72), f">> {closer}", font=f_cl, fill=acc_rgb)

    # ── LEGEND PILLS (ENTRY / SL / TARGET) — ASCII only ──────────────────────
    leg_y  = CANVAS_H - 56
    f_leg  = _font("bold", 22)
    pill_x = 40
    for label, rgb in [
        ("ENTRY", entry_rgb),
        ("SL",    sl_rgb),
        ("TARGET", target_rgb),
    ]:
        pw, ph = _text_size(draw, label, f_leg)
        # Coloured filled pill
        draw.rounded_rectangle(
            [pill_x - 14, leg_y - 6, pill_x + pw + 14, leg_y + ph + 6],
            radius=8, fill=rgb,
        )
        draw.text((pill_x, leg_y), label, font=f_leg, fill=(255, 255, 255))
        pill_x += pw + 40

    # ── BRANDING (bottom-right) — timeless (no date) ─────────────────────────
    brand_str = "StocksWithGaurav"
    f_brand   = _font("bold", 22)
    bw, bh    = _text_size(draw, brand_str, f_brand)
    draw.text((CANVAS_W - bw - 36, CANVAS_H - bh - 14),
              brand_str, font=f_brand, fill=muted_rgb)

    # ── Small logo in footer (bottom-left, subtle) ────────────────────────────
    logo = _get_logo()
    if logo is not None:
        logo_h_px = bh + 10
        logo_w_px = int(logo.width * logo_h_px / logo.height)
        logo_sm   = logo.resize((logo_w_px, logo_h_px), Image.LANCZOS)
        r, g, b, a = logo_sm.split()
        a = a.point(lambda x: int(x * 0.55))
        logo_sm = Image.merge("RGBA", (r, g, b, a))
        canvas.paste(logo_sm, (36, CANVAS_H - logo_h_px - 14), logo_sm)

    # ── SYMBOL TAG (top-left of chart area) ──────────────────────────────────
    sym_clean = chart_display_symbol(symbol)
    sym_str   = f"  {sym_clean}  ·  5m  "
    f_sym     = _font("bold", 21)
    sw, sh    = _text_size(draw, sym_str, f_sym)
    tag_y0    = HEADER_H + 10
    draw.rounded_rectangle(
        [30, tag_y0, 30 + sw, tag_y0 + sh + 10],
        radius=6, fill=_hex_to_rgb(pal["label_bg"]),
    )
    draw.text((30, tag_y0 + 4), sym_str, font=f_sym, fill=acc_rgb)

    return canvas


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT OVERLAY ENGINE
# Loads a user-provided chart image (e.g. TradingView export) and draws
# ENTRY / SL / TARGET lines + optional zone box + directional arrow on top.
# All positions are given as fractions (0.0–1.0) of the image dimensions so
# the overlay adapts to any screenshot resolution without hardcoding pixels.
# ═══════════════════════════════════════════════════════════════════════════════

def load_real_chart(image_path: str | Path) -> Image.Image:
    """
    Load a screenshot from disk and return a PIL Image (RGB).
    Raises FileNotFoundError if the path does not exist.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Chart screenshot not found: {path}")
    img = Image.open(path).convert("RGB")
    log.info("Loaded real chart screenshot: %s (%dx%d)", path.name, img.width, img.height)
    return img


def _fit_screenshot(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Fit screenshot into (target_w x target_h) with letterboxing (black bars).
    Preserves aspect ratio — never distorts the chart.
    """
    src_w, src_h = img.size
    scale   = min(target_w / src_w, target_h / src_h)
    new_w   = int(src_w * scale)
    new_h   = int(src_h * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas, (offset_x, offset_y, new_w, new_h)


def _draw_screenshot_overlays(
    img: Image.Image,
    strategy: dict,
    pal: dict,
    *,
    entry_frac: float   = 0.62,   # fraction down from top of the image
    sl_frac: float      = 0.72,
    target_frac: float  = 0.40,
    zone_frac: tuple    | None = None,   # (top_frac, bottom_frac) for OB/FVG box
    arrow: bool         = True,
) -> Image.Image:
    """
    Draw ENTRY / SL / TARGET horizontal lines and optional zone + arrow
    on a copy of *img*.  All y-positions are fractions of image height;
    x-positions span full width minus a small right margin for labels.

    Parameters
    ----------
    entry_frac  : 0.0 = top, 1.0 = bottom of image
    sl_frac     : should be > entry_frac (lower on chart = higher price only
                  for bearish; for bullish SL is below entry)
    target_frac : should be < entry_frac (above entry for bullish)
    zone_frac   : (top_frac, bot_frac) — draws a semi-transparent OB/FVG box
    arrow       : draw a thin arrow from ENTRY toward TARGET direction
    """
    img   = img.copy()
    draw  = ImageDraw.Draw(img, "RGBA")
    W, H  = img.size

    entry_rgb  = _hex_to_rgb(pal["entry"])
    sl_rgb     = _hex_to_rgb(pal["sl"])
    target_rgb = _hex_to_rgb(pal["target"])
    label_bg   = _hex_to_rgb(pal["label_bg"])

    margin_r  = max(16, int(W * 0.02))   # right-side gap for labels
    line_w    = max(2, int(H * 0.003))   # scales with image height
    label_pad = 6
    f_lbl     = _font("bold", max(14, int(H * 0.022)))

    def _h_line_with_tag(frac: float, label: str, rgb: tuple, ls_dash: bool = True):
        y    = int(H * frac)
        x1   = margin_r
        x2   = W - margin_r

        if ls_dash:
            # Manually draw dashed line
            seg   = max(12, int(W * 0.015))
            gap   = max(6,  int(W * 0.008))
            x_cur = x1
            while x_cur < x2:
                x_end = min(x_cur + seg, x2)
                draw.line([(x_cur, y), (x_end, y)], fill=(*rgb, 220), width=line_w)
                x_cur += seg + gap
        else:
            draw.line([(x1, y), (x2, y)], fill=(*rgb, 220), width=line_w + 1)

        # Price tag box on right side
        tag_text = f" {label} "
        try:
            _, _, tw, th = draw.textbbox((0, 0), tag_text, font=f_lbl)
        except Exception:
            tw, th = len(tag_text) * 10, 20
        tx = W - margin_r - tw - label_pad * 2
        ty = y - th // 2 - label_pad
        draw.rounded_rectangle(
            [tx, ty, tx + tw + label_pad * 2, ty + th + label_pad * 2],
            radius=5, fill=(*rgb, 230),
        )
        draw.text((tx + label_pad, ty + label_pad), tag_text, font=f_lbl, fill=(255, 255, 255))

    # ── Draw zone rectangle (OB / FVG) ────────────────────────────────────────
    if zone_frac:
        zt, zb = zone_frac
        y_top = int(H * zt)
        y_bot = int(H * zb)
        zone_colour = _hex_to_rgb(pal.get("ob_fill", "#ff6f00"))
        draw.rectangle([margin_r, y_top, W - margin_r, y_bot],
                       fill=(*zone_colour, 40), outline=(*zone_colour, 140), width=2)
        # Zone label
        f_zone = _font("regular", max(11, int(H * 0.016)))
        draw.text((margin_r + 8, y_top + 4), "ZONE", font=f_zone,
                  fill=(*zone_colour, 200))

    # ── Draw ENTRY / SL / TARGET ───────────────────────────────────────────────
    _h_line_with_tag(target_frac, "TARGET", target_rgb, ls_dash=True)
    _h_line_with_tag(entry_frac,  "ENTRY",  entry_rgb,  ls_dash=False)
    _h_line_with_tag(sl_frac,     "SL",     sl_rgb,     ls_dash=True)

    # ── Trade flow arrow (ENTRY → TARGET direction) ───────────────────────────
    if arrow:
        ax_x   = int(W * 0.78)          # right-side of chart, past most candles
        ay_start = int(H * entry_frac)
        ay_end   = int(H * target_frac)
        # Thin semi-transparent line
        draw.line([(ax_x, ay_start), (ax_x, ay_end)],
                  fill=(*target_rgb, 90), width=max(2, line_w))
        # Arrowhead triangle
        tip      = ay_end
        side     = max(6, int(H * 0.009))
        if ay_end < ay_start:   # bullish — arrow points up
            pts = [(ax_x, tip), (ax_x - side, tip + side * 2), (ax_x + side, tip + side * 2)]
        else:                   # bearish — arrow points down
            pts = [(ax_x, tip), (ax_x - side, tip - side * 2), (ax_x + side, tip - side * 2)]
        draw.polygon(pts, fill=(*target_rgb, 180))

    return img


def _overlay_fracs_for_strategy(strategy_id: str) -> dict:
    """
    Return default ENTRY / SL / TARGET y-fractions and optional zone_frac
    for each strategy so overlays land in visually sensible positions
    on a typical 5m NIFTY chart screenshot.

    Fractions are calibrated assuming the chart's price axis runs from
    top (high) to bottom (low) as on TradingView / Zerodha Kite.
    """
    _defaults = dict(
        entry_frac  = 0.62,
        sl_frac     = 0.72,
        target_frac = 0.38,
        zone_frac   = None,
        arrow       = True,
    )
    _map: dict[str, dict] = {
        "order_block": dict(
            entry_frac=0.60, sl_frac=0.70, target_frac=0.36,
            zone_frac=(0.54, 0.68),   # OB demand box
        ),
        "fvg": dict(
            entry_frac=0.58, sl_frac=0.68, target_frac=0.34,
            zone_frac=(0.50, 0.60),
        ),
        "vwap_strategy": dict(
            entry_frac=0.55, sl_frac=0.65, target_frac=0.38,
        ),
        "trend_pullback": dict(
            entry_frac=0.63, sl_frac=0.73, target_frac=0.40,
        ),
        "orb": dict(
            entry_frac=0.50, sl_frac=0.60, target_frac=0.28,
            zone_frac=(0.44, 0.52),
        ),
        "range_breakout": dict(
            entry_frac=0.48, sl_frac=0.58, target_frac=0.28,
            zone_frac=(0.42, 0.52),
        ),
        "sr_flip": dict(
            entry_frac=0.55, sl_frac=0.65, target_frac=0.36,
        ),
        "trendline_break": dict(
            entry_frac=0.58, sl_frac=0.68, target_frac=0.36,
        ),
        "consolidation_breakout": dict(
            entry_frac=0.50, sl_frac=0.60, target_frac=0.28,
            zone_frac=(0.44, 0.54),
        ),
        "momentum_scalp": dict(
            entry_frac=0.55, sl_frac=0.62, target_frac=0.38,
        ),
        "liquidity_grab": dict(
            entry_frac=0.64, sl_frac=0.74, target_frac=0.40,
            zone_frac=(0.66, 0.74),
        ),
        "breaker_block": dict(
            entry_frac=0.57, sl_frac=0.67, target_frac=0.36,
            zone_frac=(0.52, 0.62),
        ),
    }
    base = dict(_defaults)
    base.update(_map.get(strategy_id, {}))
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def render_cheatsheet_chart_strip(
    strategy_id: str,
    width: int,
    height: int,
    symbol: str | None = None,
    *,
    randomize_instrument: bool = True,
    randomize_history: bool = True,
) -> Image.Image | None:
    """
    Chart strip for the cheatsheet \"CHART REFERENCE\" panel.

    **Priority:**
    1. If ``assets/tv_charts/{strategy_id}.png`` (or .jpg) exists — use **your**
       screenshot (e.g. TradingView export), letterboxed to fit, then draw
       ENTRY/SL/TARGET overlays.
    2. Else — **Yahoo Finance** 5m OHLC + **mplfinance** (programmatic
       candles, TV-style theme). Symbol and history window can be randomized
       per call (see ``create_real_chart_post``).
    """
    w = max(200, width)
    h = max(100, height)

    env_sym = os.environ.get("CONTENT_CHART_SYMBOL", "").strip()
    if symbol and str(symbol).strip():
        ysym = str(symbol).strip()
    elif env_sym:
        es = env_sym
        ysym = es if (es.startswith("^") or es.upper().endswith(".NS")) else f"^{es.lstrip('^')}"
    elif randomize_instrument:
        ysym = pick_random_chart_instrument()
    else:
        ysym = "^NSEI"
    tag_label = chart_display_symbol(ysym)

    tv = _resolve_tv_chart_file(strategy_id)
    if tv is not None:
        try:
            log.info("Cheatsheet strip: using external chart file %s", tv.name)
            print(f"[DEBUG] Cheatsheet: using TV/export image {tv.name}")
            raw = load_real_chart(tv)
            fitted, _ = _fit_screenshot(raw, w, h)
            pal = _TV
            fracs = _overlay_fracs_for_strategy(strategy_id)
            try:
                df_ohlc = _fetch_ohlc(
                    ysym,
                    interval="5m",
                    period="1d",
                    random_window=randomize_history,
                )
                e, slv, tgt = trade_levels_from_swings(df_ohlc, strategy_id)
                dyn = trade_levels_to_overlay_fracs(e, slv, tgt, df_ohlc)
                for k, v in dyn.items():
                    fracs[k] = min(0.97, max(0.03, v))
            except Exception as exc:
                log.debug("Cheatsheet TV strip: swing fracs skipped: %s", exc)
            return _draw_screenshot_overlays(
                fitted.convert("RGBA"),
                {"id": strategy_id},
                pal,
                **fracs,
            )
        except Exception as e:
            log.warning("Cheatsheet TV image failed (%s), falling back to generated: %s", tv, e)

    try:
        df = _fetch_ohlc(
            ysym,
            interval="5m",
            period="1d",
            random_window=randomize_history,
        )
        pal = _TV
        return _render_chart_with_overlay(
            df, strategy_id, pal, tag_label,
            out_w=w, out_h=h, cheatsheet_mode=True,
        )
    except Exception as e:
        log.warning("Cheatsheet chart strip failed (%s): %s", strategy_id, e)
        return None


def create_real_chart_post(
    strategy: dict,
    symbol: str | None = None,
    *,
    force_dark: bool | None = None,
    randomize_instrument: bool = True,
    randomize_history_window: bool = True,
) -> str:
    """
    Generate a premium TradingView-style 1080×1080 chart post.

    Args:
        strategy:   dict with 'id', 'name', 'concept'
        symbol:     Yahoo Finance ticker (e.g. ``^NSEI``, ``RELIANCE.NS``). If omitted,
                    a **random** liquid NSE / index symbol is chosen each call (set
                    env ``CONTENT_CHART_SYMBOL`` to lock one ticker).
        force_dark: True=dark, False=light, None=random (90% dark)
        randomize_instrument:
            When ``symbol`` is None and env is unset, pick from :data:`CHART_INSTRUMENT_POOL`.
        randomize_history_window:
            When True, fetch multi-day 5m data and use a **random 40-candle slice**
            so price action / levels differ between runs.

    Returns:
        Absolute path to saved PNG.

    Raises:
        ChartError: on data fetch or render failure.
    """
    strategy_id = strategy.get("id", "order_block")

    env_sym = os.environ.get("CONTENT_CHART_SYMBOL", "").strip()
    if symbol and str(symbol).strip():
        yahoo_sym = str(symbol).strip()
    elif env_sym:
        es = env_sym
        yahoo_sym = es if (es.startswith("^") or es.upper().endswith(".NS")) else f"^{es.lstrip('^')}"
    elif randomize_instrument:
        yahoo_sym = pick_random_chart_instrument()
    else:
        yahoo_sym = "^NSEI"

    tag_label = chart_display_symbol(yahoo_sym)
    print(
        f"[DEBUG] Entered chart_engine: create_real_chart_post(strategy={strategy_id!r}, "
        f"yahoo={yahoo_sym!r}, tag={tag_label!r})"
    )

    tv = _resolve_tv_chart_file(strategy_id)
    if tv is not None:
        log.info("create_real_chart_post: using external chart %s (not Yahoo/mplfinance)", tv)
        print(f"[DEBUG] Using TradingView/export file: {tv} (overlays + same 1080 layout)")
        return create_post_from_screenshot(
            strategy,
            tv,
            force_dark=force_dark,
            symbol=tag_label,
        )

    use_dark = force_dark if force_dark is not None else (random.random() < 0.90)
    pal      = _TV if use_dark else _LIGHT
    print(f"[DEBUG] Using {'dark' if use_dark else 'light'} palette (Yahoo + mplfinance — no tv_charts file)")

    # 1. Fetch OHLC (random slice of history when enabled — different trade each run)
    print(f"[DEBUG] Fetching OHLC for {yahoo_sym} (random_window={randomize_history_window})...")
    df = _fetch_ohlc(
        yahoo_sym,
        interval="5m",
        period="1d",
        random_window=randomize_history_window,
    )
    print(f"[DEBUG] Fetched {len(df)} candles for {yahoo_sym}")

    # 2. Render chart with overlay
    print(f"[DEBUG] Rendering chart with overlay: {strategy_id!r}")
    chart_img = _render_chart_with_overlay(df, strategy_id, pal, tag_label)

    # 3. Composite final 1080×1080
    print("[DEBUG] Compositing final 1080×1080 image...")
    final = _composite(chart_img, strategy, pal, tag_label)

    # 4. Save
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(_OUT_DIR / f"post_real_chart_{strategy_id}_{ts}.png")
    print(f"[DEBUG] Saving chart post to: {path}")
    final.save(path, "PNG", optimize=True)
    print("[DEBUG] Chart post saved successfully")
    log.info("Real chart post saved: %s", path)
    return path


def create_post_from_screenshot(
    strategy: dict,
    image_path: str | Path,
    *,
    force_dark: bool | None = None,
    entry_frac: float | None  = None,
    sl_frac: float | None     = None,
    target_frac: float | None = None,
    zone_frac: tuple | None   = None,
    symbol: str = "NSEI",
) -> str:
    """
    Overlay ENTRY / SL / TARGET levels onto a *real* chart screenshot
    (TradingView, Zerodha Kite, or any trading platform), then composite
    the same premium 1080×1080 header + footer around it.

    Args:
        strategy    : dict with 'id', 'name', 'concept'.
        image_path  : Path to the chart screenshot PNG/JPG.
        force_dark  : True=dark palette, False=light, None=auto (90% dark).
        entry_frac  : Override entry line position (0.0=top, 1.0=bottom).
        sl_frac     : Override SL line position.
        target_frac : Override target line position.
        zone_frac   : Override zone box (top_frac, bot_frac) or None to skip.
        symbol      : Display label for the symbol tag (default "NSEI").

    Returns:
        Absolute path to saved PNG.

    Raises:
        FileNotFoundError : if image_path does not exist.
        ChartError        : on rendering / save failure.

    Fallback:
        If image_path is missing, automatically calls create_real_chart_post().
    """
    strategy_id = strategy.get("id", "order_block")
    print(f"[DEBUG] create_post_from_screenshot: strategy={strategy_id!r}, image={image_path!r}")

    # ── Fallback guard ─────────────────────────────────────────────────────────
    if not Path(image_path).exists():
        log.warning("Screenshot not found (%s) — falling back to mplfinance chart.", image_path)
        print(f"[DEBUG] Screenshot missing — fallback to create_real_chart_post()")
        return create_real_chart_post(strategy, force_dark=force_dark)

    use_dark = force_dark if force_dark is not None else (random.random() < 0.90)
    pal      = _TV if use_dark else _LIGHT
    print(f"[DEBUG] Using {'dark' if use_dark else 'light'} palette")

    # ── Load + fit screenshot ──────────────────────────────────────────────────
    print(f"[DEBUG] Loading screenshot...")
    raw_img = load_real_chart(image_path)
    fitted, _bounds = _fit_screenshot(raw_img, CANVAS_W, CHART_H)
    print(f"[DEBUG] Screenshot fitted to {CANVAS_W}x{CHART_H}")

    # ── Determine overlay fractions ────────────────────────────────────────────
    fracs = _overlay_fracs_for_strategy(strategy_id)
    try:
        ysym = _yahoo_symbol_for_chart_fetch(symbol)
        df_ohlc = _fetch_ohlc(
            ysym,
            interval="5m",
            period="1d",
            random_window=True,
        )
        e, slv, tgt = trade_levels_from_swings(df_ohlc, strategy_id)
        dyn = trade_levels_to_overlay_fracs(e, slv, tgt, df_ohlc)
        for k, v in dyn.items():
            fracs[k] = min(0.97, max(0.03, v))
        log.debug(
            "Screenshot overlays from swings: entry=%.2f sl=%.2f tgt=%.2f",
            fracs.get("entry_frac", 0),
            fracs.get("sl_frac", 0),
            fracs.get("target_frac", 0),
        )
    except Exception as exc:
        log.debug("Swing-based screenshot fracs skipped: %s", exc)

    if entry_frac  is not None:
        fracs["entry_frac"]  = entry_frac
    if sl_frac     is not None:
        fracs["sl_frac"]     = sl_frac
    if target_frac is not None:
        fracs["target_frac"] = target_frac
    if zone_frac   is not None:
        fracs["zone_frac"]   = zone_frac

    # ── Draw overlays ──────────────────────────────────────────────────────────
    print(f"[DEBUG] Drawing overlays: entry={fracs['entry_frac']:.2f} "
          f"sl={fracs['sl_frac']:.2f} target={fracs['target_frac']:.2f}")
    overlaid = _draw_screenshot_overlays(fitted, strategy, pal, **fracs)

    # ── Composite header + overlaid chart + footer ────────────────────────────
    print("[DEBUG] Compositing final 1080x1080...")
    # _composite expects a PIL Image for the chart slot; feed the overlaid one.
    # We pass a minimal strategy dict ensuring 'concept' is always present.
    final = _composite(
        overlaid.convert("RGBA"),
        strategy,
        pal,
        symbol,
    )

    # ── Save ───────────────────────────────────────────────────────────────────
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(_OUT_DIR / f"post_screenshot_{strategy_id}_{ts}.png")
    print(f"[DEBUG] Saving to: {path}")
    final.save(path, "PNG", optimize=True)
    print("[DEBUG] Screenshot post saved successfully")
    log.info("Screenshot post saved: %s", path)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# TV PLAYWRIGHT → SCREENSHOT POST OR MPLFINANCE FALLBACK
# ═══════════════════════════════════════════════════════════════════════════════


def use_real_chart(
    chart_path: str | Path,
    strategy: dict,
    *,
    symbol: str = "NSEI",
    force_dark: bool | None = None,
    entry_frac: float | None = None,
    sl_frac: float | None = None,
    target_frac: float | None = None,
    zone_frac: tuple | None = None,
) -> str:
    """
    Build the standard 1080×1080 post from an existing chart image path
    (e.g. Playwright ``create_tradingview_screenshot`` output).

    Wraps ``create_post_from_screenshot`` (ENTRY/SL/TARGET overlays + header/footer).

    Example::

        chart_path = create_tradingview_screenshot("NSE:NIFTY")
        if chart_path:
            out = use_real_chart(chart_path, strategy, symbol="NIFTY")
    """
    return create_post_from_screenshot(
        strategy,
        chart_path,
        symbol=symbol,
        force_dark=force_dark,
        entry_frac=entry_frac,
        sl_frac=sl_frac,
        target_frac=target_frac,
        zone_frac=zone_frac,
    )


def fallback_to_mplfinance(
    strategy: dict,
    *,
    symbol: str = "^NSEI",
    force_dark: bool | None = None,
) -> str:
    """
    Yahoo Finance OHLC + mplfinance chart + same composite layout.

    Example::

        out = fallback_to_mplfinance(strategy)
    """
    return create_real_chart_post(
        strategy,
        symbol=symbol,
        force_dark=force_dark,
        randomize_history_window=True,
    )


def create_chart_post_tv_or_mplfinance(
    strategy: dict,
    *,
    tv_symbol: str = "NSE:NIFTY",
    tv_timeframe: str = "5",
    headless: bool = True,
    force_dark: bool | None = None,
) -> str:
    """
    Try Playwright TradingView capture first; if it fails, use mplfinance.

    Equivalent to::

        chart_path = create_tradingview_screenshot(tv_symbol, tv_timeframe, headless=headless)
        if chart_path:
            tag = tv_symbol.split(":")[-1] if ":" in tv_symbol else tv_symbol
            return use_real_chart(chart_path, strategy, symbol=tag, force_dark=force_dark)
        return fallback_to_mplfinance(strategy, force_dark=force_dark)

    Requires ``playwright`` installed and ``playwright install`` run for TV capture.
    """
    try:
        from content_engine.services.tradingview_screenshot import create_tradingview_screenshot
    except ImportError:
        log.warning("tradingview_screenshot unavailable — using mplfinance fallback")
        return fallback_to_mplfinance(strategy, force_dark=force_dark)

    chart_path = create_tradingview_screenshot(tv_symbol, tv_timeframe, headless=headless)
    if chart_path:
        tag = tv_symbol.split(":")[-1] if ":" in tv_symbol else tv_symbol
        return use_real_chart(chart_path, strategy, symbol=tag, force_dark=force_dark)
    return fallback_to_mplfinance(strategy, force_dark=force_dark)
