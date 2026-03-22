"""
content_engine/services/tradingview_screenshot.py

Production-grade TradingView chart capture via Playwright + **lightweight widget embed**
(s.tradingview.com/widgetembed) — no full chart app, no login wall, consistent layout.

Public API:
    create_tradingview_screenshot(symbol="NSE:NIFTY", timeframe="5") -> str | None
    close_tradingview_browser()  # optional: release pooled Chromium

Requires: pip install playwright && playwright install

Post-capture crop: estimates background from **edge pixels**, finds the **foreground**
(candles/grid/labels), crops to that bounding box + **10% padding**, else **center trim**.
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger("content_engine.tradingview_screenshot")

_HERE = Path(__file__).resolve().parent.parent
_OUT_DIR = _HERE / "generated_posts"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

_TIMEFRAME_MINUTES = {"1", "3", "5", "15", "30", "60", "120", "240", "D", "W", "M"}

# ── Optional process-wide browser reuse (multiple captures per run) ─────────────
_pw: Any = None
_browser: Any = None


def close_tradingview_browser() -> None:
    """Close pooled Chromium + Playwright (call on shutdown or after batch)."""
    global _pw, _browser
    try:
        if _browser is not None:
            _browser.close()
    except Exception as e:
        log.debug("Browser close: %s", e)
    finally:
        _browser = None
    try:
        if _pw is not None:
            _pw.stop()
    except Exception as e:
        log.debug("Playwright stop: %s", e)
    finally:
        _pw = None


def _sanitize_symbol_for_file(symbol: str) -> str:
    s = symbol.strip().upper().replace(":", "_").replace("/", "_").replace(" ", "_")
    return re.sub(r"[^\w\-]", "", s) or "chart"


def _normalize_symbol(symbol: str) -> str:
    s = symbol.strip()
    if ":" not in s:
        return f"NSE:{s}"
    return s


def _build_widget_embed_url(symbol: str, timeframe: str) -> str:
    """
    Lightweight embed — no login, minimal chrome, stable layout.

    https://s.tradingview.com/widgetembed/?symbol=...&interval=...&theme=dark&style=1
    """
    sym = _normalize_symbol(symbol)
    tf = timeframe.strip()
    if tf.upper() in ("D", "W", "M"):
        interval = tf.upper()
    else:
        interval = tf
    params = {
        "symbol": sym,
        "interval": interval,
        "theme": "dark",
        "style": "1",  # candles
        "hide_side_toolbar": "true",
        "hide_top_toolbar": "false",
        "allow_symbol_change": "false",
        "save_image": "false",
        "locale": "en",
    }
    q = urlencode(params, safe=":")
    return f"https://s.tradingview.com/widgetembed/?{q}"


def _get_playwright_browser(headless: bool):
    """Return (playwright, browser) — reuses pooled browser when possible."""
    global _pw, _browser
    from playwright.sync_api import sync_playwright

    if _browser is not None:
        try:
            if _browser.is_connected():
                return _pw, _browser
        except Exception:
            pass
        close_tradingview_browser()

    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    return _pw, _browser


def _find_canvas_scope(page: Any) -> Any:
    """
    Return Frame or Page that contains the main chart canvas (widget may use iframe).
    """
    try:
        if page.locator("canvas").count() > 0:
            return page
    except Exception:
        pass
    for fr in page.frames:
        try:
            if fr == page.main_frame:
                continue
            if fr.locator("canvas").count() > 0:
                return fr
        except Exception:
            continue
    return page


def _canvas_locator(scope: Any):
    return scope.locator("canvas").first


def _is_blank_or_low_detail(path: Path, std_threshold: float = 3.0) -> bool:
    """Detect empty / failed loads (flat color, almost no structure). PIL-only."""
    try:
        from PIL import Image, ImageStat

        im = Image.open(path).convert("L")
        st = ImageStat.Stat(im)
        if not st.stddev:
            return True
        if st.stddev[0] < std_threshold:
            return True
        return False
    except Exception as e:
        log.debug("Blank check failed: %s", e)
        return True


def _pil_resample_lanczos():
    from PIL import Image

    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _rgb_composite_on_black(path: Path):
    """RGBA → RGB on black (TradingView dark theme)."""
    from PIL import Image

    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGB", im.size, (0, 0, 0))
    bg.paste(im, mask=im.split()[3])
    return bg


def _sample_edge_pixels(im: Any, strip: int) -> list[tuple[int, int, int]]:
    """Collect RGB samples from a border strip (background reference)."""
    w, h = im.size
    strip = max(2, min(strip, w // 5, h // 5))
    pixels: list[tuple[int, int, int]] = []
    px = im.load()
    for y in range(strip):
        for x in range(w):
            pixels.append(px[x, y][:3])
    for y in range(h - strip, h):
        for x in range(w):
            pixels.append(px[x, y][:3])
    for y in range(h):
        for x in range(strip):
            pixels.append(px[x, y][:3])
    for y in range(h):
        for x in range(w - strip, w):
            pixels.append(px[x, y][:3])
    return pixels


def _median_rgb(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not pixels:
        return (0, 0, 0)
    rs = [p[0] for p in pixels]
    gs = [p[1] for p in pixels]
    bs = [p[2] for p in pixels]
    return (
        int(statistics.median(rs)),
        int(statistics.median(gs)),
        int(statistics.median(bs)),
    )


def _channel_diff(p: tuple[int, int, int], bg: tuple[int, int, int]) -> int:
    return max(abs(p[0] - bg[0]), abs(p[1] - bg[1]), abs(p[2] - bg[2]))


def _foreground_bbox(
    im: Any,
    bg: tuple[int, int, int],
    diff_threshold: int,
) -> tuple[int, int, int, int] | None:
    """
    Bounding box of pixels that differ from background (candles, grid, labels).
    Coordinates inclusive: min_x, min_y, max_x, max_y.
    """
    w, h = im.size
    px = im.load()
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    found = False
    for y in range(h):
        for x in range(w):
            p = px[x, y][:3]
            if _channel_diff(p, bg) >= diff_threshold:
                found = True
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if not found or max_x < min_x or max_y < min_y:
        return None
    return (min_x, min_y, max_x, max_y)


def _adaptive_diff_threshold(edge_pixels: list[tuple[int, int, int]], bg: tuple[int, int, int]) -> int:
    """Edge deviation from median bg → noise scale; keep candles/grid visible."""
    if len(edge_pixels) < 8:
        return 16
    diffs = [_channel_diff(p, bg) for p in edge_pixels]
    mad = statistics.median(abs(d - statistics.median(diffs)) for d in diffs)
    # Slightly above typical border noise; cap so faint grid lines still count as foreground
    return max(12, min(38, int(4.0 * mad + 6)))


def _crop_to_candle_cluster(
    path: Path,
    *,
    padding_frac: float = 0.10,
    edge_strip: int = 14,
    max_analysis_side: int = 520,
    min_bbox_area_frac: float = 0.035,
    min_side_frac: float = 0.06,
) -> bool:
    """
    Detect active chart region (non-background pixels vs edge-estimated bg),
    crop with padding_frac margin. Returns True if applied, False to use fallback.
    """
    try:
        from PIL import Image

        im_full = _rgb_composite_on_black(path)
        w0, h0 = im_full.size
        if w0 < 32 or h0 < 32:
            return False

        scale = min(1.0, max_analysis_side / max(w0, h0))
        nw = max(32, int(round(w0 * scale)))
        nh = max(32, int(round(h0 * scale)))
        resample = _pil_resample_lanczos()
        small = im_full.resize((nw, nh), resample).convert("RGB")

        edge_px = _sample_edge_pixels(small, edge_strip)
        bg = _median_rgb(edge_px)
        diff_thr = _adaptive_diff_threshold(edge_px, bg)

        bbox = _foreground_bbox(small, bg, diff_thr)
        if bbox is None:
            return False

        min_x, min_y, max_x, max_y = bbox
        bw = max_x - min_x + 1
        bh = max_y - min_y + 1
        area_frac = (bw * bh) / float(nw * nh)
        if area_frac < min_bbox_area_frac:
            return False
        if bw < nw * min_side_frac or bh < nh * min_side_frac:
            return False

        # Map bbox from analysis image back to full resolution (float geometry)
        left = (min_x / nw) * w0
        top = (min_y / nh) * h0
        right = ((max_x + 1) / nw) * w0
        bottom = ((max_y + 1) / nh) * h0

        rw = right - left
        rh = bottom - top
        pad_x = rw * padding_frac
        pad_y = rh * padding_frac

        crop_left = max(0.0, left - pad_x)
        crop_top = max(0.0, top - pad_y)
        crop_right = min(float(w0), right + pad_x)
        crop_bottom = min(float(h0), bottom + pad_y)

        if crop_right - crop_left < 16 or crop_bottom - crop_top < 16:
            return False

        box = (
            int(crop_left),
            int(crop_top),
            int(round(crop_right)),
            int(round(crop_bottom)),
        )
        cropped = im_full.crop(box)
        cropped.save(path, "PNG", optimize=True)
        log.debug(
            "Candle-cluster crop: bbox=%s thr=%d area_frac=%.3f",
            box,
            diff_thr,
            area_frac,
        )
        return True
    except Exception as e:
        log.debug("Candle-cluster crop failed: %s", e)
        return False


def _center_crop_image(path: Path, margin_frac: float = 0.03) -> None:
    """Trim edge margins uniformly (fallback when foreground detection fails)."""
    try:
        from PIL import Image

        im = Image.open(path).convert("RGBA")
        w, h = im.size
        mx = max(1, int(w * margin_frac))
        my = max(1, int(h * margin_frac))
        if w <= 2 * mx or h <= 2 * my:
            return
        cropped = im.crop((mx, my, w - mx, h - my))
        cropped.save(path, "PNG", optimize=True)
    except Exception as e:
        log.debug("Center crop skipped: %s", e)


def _crop_chart_region(path: Path) -> None:
    """Prefer candle-cluster bbox + padding; else center margin trim."""
    if not _crop_to_candle_cluster(path):
        log.debug("Foreground bbox unavailable; center-crop fallback")
        _center_crop_image(path)


def _run_capture_once(
    symbol: str,
    timeframe: str,
    headless: bool,
    *,
    reuse_browser: bool = True,
) -> str | None:
    symbol = _normalize_symbol(symbol)
    url = _build_widget_embed_url(symbol, timeframe)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"tv_{_sanitize_symbol_for_file(symbol)}_{ts}.png"
    out_path = _OUT_DIR / fname

    pw_local: Any = None
    browser: Any = None
    if reuse_browser:
        pw_local, browser = _get_playwright_browser(headless)
    else:
        from playwright.sync_api import sync_playwright

        pw_local = sync_playwright().start()
        browser = pw_local.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        device_scale_factor=1,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    page = context.new_page()

    try:
        log.info("TV widget load: %s", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        deadline = time.monotonic() + 15.0
        scope = None
        while time.monotonic() < deadline:
            scope = _find_canvas_scope(page)
            if scope.locator("canvas").count() > 0:
                break
            time.sleep(0.25)
        else:
            log.warning("TV fallback triggered: no canvas within 15s")
            return None

        time.sleep(2.0)

        try:
            scope.evaluate(
                """
                () => {
                  const c = document.querySelector('canvas');
                  if (c) { c.scrollIntoView({block:'center', inline:'center'}); }
                }
                """
            )
        except Exception as e:
            log.debug("scrollIntoView: %s", e)

        try:
            canvas = _canvas_locator(scope)
            box = canvas.bounding_box()
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.wheel(0, -120)
                time.sleep(0.4)
        except Exception as e:
            log.debug("Wheel zoom: %s", e)

        time.sleep(0.5)

        canvas = _canvas_locator(scope)
        try:
            canvas.wait_for(state="visible", timeout=5_000)
            canvas.screenshot(path=str(out_path), type="png")
        except Exception as e:
            log.warning("Canvas element screenshot failed (%s); viewport fallback", e)
            page.screenshot(path=str(out_path), full_page=False)

        if not out_path.exists() or out_path.stat().st_size < 2_000:
            log.warning("TV fallback triggered: missing or tiny file")
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

        _crop_chart_region(out_path)

        if _is_blank_or_low_detail(out_path):
            log.warning("TV fallback triggered: blank or flat image")
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

        log.info("TV screenshot success")
        log.debug("TV screenshot path: %s", out_path)
        return str(out_path.resolve())

    finally:
        try:
            context.close()
        except Exception:
            pass
        if not reuse_browser:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw_local.stop()
            except Exception:
                pass


def create_tradingview_screenshot(
    symbol: str = "NSE:NIFTY",
    timeframe: str = "5",
    *,
    headless: bool = True,
    reuse_browser: bool = True,
) -> str | None:
    """
    Capture a TradingView chart via **widget embed** (clean, no login).

    Parameters
    ----------
    symbol
        e.g. ``NSE:NIFTY``, ``NSE:BANKNIFTY``. Bare ``NIFTY`` → ``NSE:NIFTY``.
    timeframe
        Minute string ``"5"``, ``"15"``, or ``"D"`` / ``"W"``.
    headless
        Run Chromium headless (False to debug).
    reuse_browser
        Keep one Chromium instance for repeated calls in the same process.
        Call ``close_tradingview_browser()`` when done with a batch.

    Returns
    -------
    str | None
        Path to ``generated_posts/tv_{symbol}_{timestamp}.png``, or ``None`` if
        canvas missing, capture failed, or image looks blank (after retries).
    """
    tf = timeframe.strip()
    if tf not in _TIMEFRAME_MINUTES and not tf.isdigit():
        log.warning("Unusual timeframe %r — proceeding anyway", tf)

    sym = _normalize_symbol(symbol)
    last_exc: Exception | None = None

    for attempt in range(1, 4):
        try:
            path = _run_capture_once(sym, tf, headless=headless, reuse_browser=reuse_browser)
            if path and Path(path).exists():
                return path
            log.warning("TV fallback triggered: attempt %d/3 returned no file", attempt)
        except Exception as e:
            last_exc = e
            log.warning(
                "TradingView capture attempt %d/3 failed: %s",
                attempt,
                e,
                exc_info=log.isEnabledFor(logging.DEBUG),
            )
            log.warning("TV fallback triggered: retrying after error")
            time.sleep(1.5 * attempt)

    if last_exc:
        log.error("TV fallback triggered: exhausted retries (%s)", last_exc)
    else:
        log.error("TV fallback triggered: exhausted retries (no exception)")
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = create_tradingview_screenshot("NSE:NIFTY", "5", headless=True)
    print(out or "FAILED")
    close_tradingview_browser()
