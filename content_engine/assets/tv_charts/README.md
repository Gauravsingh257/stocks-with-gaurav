# TradingView / manual chart images (optional)

## What the code uses by default

If **no file** is present here, charts are **not** from Zerodha and **not** from tradingview.com.

They are built from:

- **Prices:** Yahoo Finance (`yfinance`) — e.g. NIFTY `^NSEI` 5m  
- **Drawing:** `mplfinance` + matplotlib — dark theme meant to look like TV

So they are **generated** charts, not a screenshot of your broker or TradingView.

## Use your real TradingView chart

1. Export or screenshot your chart from **tradingview.com** (PNG/JPG).
2. Rename it to match the **strategy id** + extension, e.g.:
   - `fvg.png`
   - `order_block.png`
   - `vwap_strategy.png`
3. Put the file in **this folder** (`content_engine/assets/tv_charts/`).

Or set:

```text
CONTENT_TV_CHART_DIR=C:\path\to\your\folder
```

and put the same filenames there.

Then:

- **Full posts** (`create_real_chart_post`) use your image + ENTRY/SL/TARGET overlays + header/footer.
- **Cheatsheet** chart strip uses your image + overlays in the reference panel.

**Strategy ids** (same as in `strategy_agent.py`):  
`order_block`, `fvg`, `liquidity_grab`, `breaker_block`, `trend_pullback`, `range_breakout`, `vwap_strategy`, `orb`, `sr_flip`, `trendline_break`, `consolidation_breakout`, `momentum_scalp`.
