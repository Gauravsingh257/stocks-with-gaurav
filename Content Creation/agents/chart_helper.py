"""
Content Creation / agents / chart_helper.py

Generate mini candlestick stock charts for carousel slides.
Uses yfinance (primary) for OHLC data — handles Yahoo Finance cookie/crumb auth
automatically so all NSE symbols work reliably. Falls back to raw HTTP for
index symbols that yfinance may be slow on.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger("content_creation.agents.chart")

# Candlestick colors (light theme)
_COLOR_BULL = "#059669"   # emerald green body for up candles
_COLOR_BEAR = "#DC2626"   # crimson red body for down candles
_COLOR_WICK = "#9CA3AF"   # gray wick/shadow color
_BG_COLOR = "#F5F3EF"     # warm off-white background


# Map internal symbol names → Yahoo Finance tickers
# yfinance uses plain .NS suffix for NSE equities; no URL encoding needed.
_TICKER_MAP: dict[str, str] = {
    "NIFTY_50":  "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "M&M":       "M&M.NS",  # yfinance handles & natively
}


def generate_mini_chart(
    symbol: str,
    *,
    period: str = "1mo",
    interval: str = "1d",
    accent_color: str = "#00ffcc",
    support: float = 0,
    resistance: float = 0,
    output_dir: str | None = None,
) -> str | None:
    """
    Generate a mini candlestick chart PNG on dark background.

    Returns the path to the saved PNG file, or None on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.ticker import FuncFormatter
    except ImportError:
        log.warning("matplotlib not installed — skipping chart generation")
        return None

    # ── Fetch OHLC via yfinance ─────────────────────────────────────────────
    # yfinance manages cookie/crumb auth automatically, so NSE symbols that
    # fail with raw HTTP (e.g. TATAMOTORS.NS) work perfectly here.
    try:
        import yfinance as yf
        from datetime import datetime

        # Resolve internal name → Yahoo Finance ticker
        yf_ticker = _TICKER_MAP.get(symbol, f"{symbol}.NS")

        # For index symbols (^...) use as-is
        if symbol.startswith("^"):
            yf_ticker = symbol

        df = yf.Ticker(yf_ticker).history(period=period, interval=interval, auto_adjust=True)

        # If primary ticker returned nothing try wider period then give up
        if df.empty:
            log.warning("yfinance: no data for %s (period=%s), trying 3mo", yf_ticker, period)
            df = yf.Ticker(yf_ticker).history(period="3mo", interval=interval, auto_adjust=True)

        if df.empty:
            log.warning("yfinance: still no data for %s — skipping chart", yf_ticker)
            return None

        log.info("Chart fetch OK via yfinance: %s (%d bars)", yf_ticker, len(df))

        # Convert DataFrame rows → list of (datetime, O, H, L, C) tuples
        ohlc = []
        for ts, row in df.iterrows():
            try:
                dt = ts.to_pydatetime()
                o, h, lo, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
                if all(v == v for v in (o, h, lo, c)):  # NaN check
                    ohlc.append((dt, o, h, lo, c))
            except Exception:
                continue

        if len(ohlc) < 5:
            log.warning("Not enough data points for %s chart", symbol)
            return None

    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", symbol, e)
        return None

    # Render candlestick chart
    try:
        fig, ax = plt.subplots(figsize=(9.6, 3.2), dpi=100)
        fig.patch.set_facecolor(_BG_COLOR)
        ax.set_facecolor(_BG_COLOR)

        n = len(ohlc)

        # For daily data or intraday data (many bars), use integer index x-axis
        # to avoid clustered candles + overnight/weekend gaps
        use_index_axis = n > 15

        if use_index_axis:
            candle_w = 0.7
            wick_lw = 0.8
        else:
            candle_w = 0.6
            wick_lw = 1.0

        # Minimum visible body: 4% of the total H-L price range so doji/near-doji
        # candles still render as a clearly visible rectangle instead of a hairline.
        # At figure size 960×320px with 8% y-padding this maps to ~8px minimum,
        # making even extreme dojis readable regardless of the stock's price level.
        all_lows_raw  = [lo for _, _, _, lo, _ in ohlc]
        all_highs_raw = [h  for _, _, h,  _, _ in ohlc]
        price_range = max(all_highs_raw) - min(all_lows_raw)
        min_body_h = price_range * 0.04

        for i, (dt, o, h, lo, c) in enumerate(ohlc):
            x = i if use_index_axis else mdates.date2num(dt)
            is_bull = c >= o

            body_color = _COLOR_BULL if is_bull else _COLOR_BEAR
            wick_color = body_color

            body_bottom = min(o, c)
            raw_body   = abs(c - o)
            # Enforce minimum body height so even doji candles are visible
            body_height = max(raw_body, min_body_h)
            # Centre the expanded body on the mid-price so it stays symmetric
            if body_height > raw_body:
                mid = (o + c) / 2
                body_bottom = mid - body_height / 2

            # Wick (high-low line)
            ax.plot([x, x], [lo, h],
                    color=wick_color, linewidth=wick_lw, alpha=0.8, solid_capstyle="round")

            # Body (rectangle)
            half_w = candle_w * 0.5
            rect = plt.Rectangle(
                (x - half_w, body_bottom),
                candle_w, body_height,
                facecolor=body_color,
                edgecolor=body_color,
                alpha=0.9,
                linewidth=0.5,
            )
            ax.add_patch(rect)

        # S/R level lines
        if support > 0:
            ax.axhline(y=support, color="#DC2626", linestyle="--",
                       linewidth=1.2, alpha=0.7, label=f"S: {support:,.0f}")
        if resistance > 0:
            ax.axhline(y=resistance, color="#059669", linestyle="--",
                       linewidth=1.2, alpha=0.7, label=f"R: {resistance:,.0f}")

        # Auto-scale Y with some padding (reuse pre-calculated arrays)
        y_min, y_max = min(all_lows_raw), max(all_highs_raw)
        y_pad = (y_max - y_min) * 0.08
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        # Auto-scale X with padding
        if use_index_axis:
            ax.set_xlim(-1, n)
            # Show date labels at day boundaries (max 6 to avoid crowding)
            day_labels: list[tuple[int, str]] = []
            prev_date = None
            for idx, (dt, *_) in enumerate(ohlc):
                d = dt.date()
                if d != prev_date:
                    day_labels.append((idx, dt.strftime("%d %b")))
                    prev_date = d
            # Thin labels if too many
            if len(day_labels) > 6:
                step = max(1, len(day_labels) // 5)
                day_labels = day_labels[::step]
            if day_labels:
                ax.set_xticks([pos for pos, _ in day_labels])
                ax.set_xticklabels([lbl for _, lbl in day_labels])
        else:
            all_dates = [mdates.date2num(dt) for dt, _, _, _, _ in ohlc]
            ax.set_xlim(min(all_dates) - 1, max(all_dates) + 1)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
            # Max 6 ticks to avoid crowded labels
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))

        # Styling
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#D1D5DB")
        ax.spines["bottom"].set_color("#D1D5DB")
        ax.tick_params(colors="#6B7280", labelsize=8)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))

        if support > 0 or resistance > 0:
            ax.legend(loc="upper left", fontsize=9,
                      facecolor=_BG_COLOR, edgecolor="#D1D5DB",
                      labelcolor="#374151")

        ax.grid(True, alpha=0.3, color="#D1D5DB")
        plt.tight_layout(pad=0.5)

        # Save
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            out = out / f"chart_{symbol}.png"
        else:
            out = Path(tempfile.mkdtemp()) / f"chart_{symbol}.png"

        fig.savefig(str(out), facecolor=_BG_COLOR, bbox_inches="tight",
                    pad_inches=0.1, transparent=True)
        plt.close(fig)
        log.info("Generated chart for %s -> %s", symbol, out)
        return str(out)

    except Exception as e:
        log.warning("Chart render failed for %s: %s", symbol, e)
        return None
