"""
content_engine/services/image_generator.py

Converts trading strategy and news intelligence dicts into
Instagram-ready 1080×1080 PNG images — clean, minimal, tradfinx-style.

Public API:
    generate_strategy_image(post: dict)              -> str (file path)
    generate_news_image(news_item: dict)             -> str (file path)
    create_visual_strategy_diagram(strategy_name)    -> str (file path)

Diagram templates (used by create_visual_strategy_diagram):
    - order_block        Consolidation → breakout → OB retest → continuation
    - fvg                Impulse gap → fill → continuation
    - liquidity_grab     Equal highs swept → sharp reversal
    - breaker_block      OB broken → flip → retest rejection
    - trend_pullback     Uptrend → EMA dip → bounce → continuation
    - range_breakout     Sideways box → volume breakout → measured move
    - vwap               Price below VWAP → reclaim → rally
    - orb                15-min ORB range → breakout
    - sr_flip            Support broken → retest as resistance → drop
    - trendline_break    Rising trendline → break → retest → drop
    - consolidation      Impulse → tight flag → breakout
    - momentum_scalp     Fast 3-candle burst → entry → quick target
"""

from __future__ import annotations

import io
import logging
import os
import random
import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("content_engine.image_generator")

# ── Output directory ──────────────────────────────────────────────────────────
_HERE = Path(__file__).parent.parent          # content_engine/
_OUT_DIR = _HERE / "generated_posts"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Canvas constants ──────────────────────────────────────────────────────────
W, H        = 1080, 1080
MARGIN      = 72          # outer padding on each side
INNER_W     = W - 2 * MARGIN

# ── Colour palette ────────────────────────────────────────────────────────────
C_BG         = (255, 255, 255)     # white background
C_ACCENT     = (15,  80, 180)      # deep blue — title, icons, lines
C_TITLE_BAR  = (15,  80, 180)      # solid title-bar fill
C_TITLE_TXT  = (255, 255, 255)     # white title text on blue bar
C_SECTION_LBL= (15,  80, 180)      # blue section labels
C_BODY       = (30,  30,  30)      # near-black body text
C_BULLET     = (15,  80, 180)      # bullet dot colour
C_DIVIDER    = (220, 220, 220)     # light grey rule
C_BRAND_BG   = (15,  80, 180)      # footer bar
C_BRAND_TXT  = (255, 255, 255)     # footer text
C_TAG_BG     = (236, 242, 255)     # light blue tag pill background
C_CONFIDENCE = {
    "high":   (220,  50,  50),
    "medium": (230, 160,   0),
    "low":    (60,  160,  60),
}
# Highlight colours (ENTRY / SL / TARGET) — viral template
C_ENTRY_HI   = (22,  135,  60)
C_SL_HI      = (200,  45,  45)
C_TARGET_HI  = (15,   80, 180)

# =====================================================
# VIRAL CONTENT ENGINE (CORE)
# =====================================================

HOOKS = [
    "SMART MONEY EXITING ⚠️",
    "THIS IS A TRAP 👀",
    "RETAIL IS WRONG AGAIN ❌",
    "BIG MONEY MOVING 🔥",
    "MOST TRADERS FAIL HERE ⚠️",
    "DON'T ENTER EARLY ⚠️",
]

PSYCHOLOGY_LINES = [
    "Most traders will buy the top",
    "This is where retail gets trapped",
    "Late entries will get punished",
    "Don't chase this move",
    "This is why SL hits",
]

WARNING_LINES = [
    "Don't enter early",
    "Wait or get stopped out",
    "This is where retail loses",
    "No confirmation = no trade",
]

TRADE_LINES = [
    "SELL RALLIES (NOT BREAKOUTS)",
    "BUY DIPS (NOT CHASE)",
    "WAIT FOR SWEEP → THEN ENTER",
    "AVOID EARLY ENTRIES",
]


def pick(arr: list) -> str:
    """Pick random element from list (viral content engine)."""
    return random.choice(arr)


def build_news_content(data: dict) -> dict[str, Any]:
    """Build viral news post content from raw data."""
    stock = data.get("stock", "MARKET")
    event = data.get("event", "").strip()
    impact = data.get("impact", "").strip()
    trade_raw = (data.get("trade_idea") or data.get("trade", "")).strip()
    title_raw = (data.get("title") or "").strip()
    title = title_raw.upper() if title_raw else f"{stock} {event}".upper()[:60]
    return enforce_rules({
        "hook": pick(HOOKS),
        "title": title or "MARKET NEWS",
        "psychology": pick(PSYCHOLOGY_LINES),
        "event": event or "—",
        "impact": impact or "—",
        "trade": trade_raw or pick(TRADE_LINES),
    })


def build_ob_content() -> dict[str, Any]:
    """Build Order Block diagram content."""
    return {
        "hook": pick([
            "SMART MONEY ENTRY 🔥",
            "MOST TRADERS FAIL HERE ⚠️",
            "DON'T MISS THIS SETUP 👀",
            "THIS IS HOW PROS ENTER 💰",
        ]),
        "warning": pick(WARNING_LINES),
    }


def build_fvg_content() -> dict[str, Any]:
    """Build FVG diagram content."""
    return {
        "hook": pick([
            "THIS MOVE PRINTS MONEY 🔥",
            "SMART MONEY RETURNS HERE 👀",
            "THIS IS THE REAL ENTRY ⚠️",
            "RETAIL MISSES THIS MOVE ❌",
        ]),
        "warning": pick(WARNING_LINES),
        "psychology": pick(PSYCHOLOGY_LINES),
    }


def style_entry() -> dict[str, Any]:
    """Entry line/arrow style."""
    return {"color": (0, 255, 136), "thickness": 4}


def style_sl() -> dict[str, Any]:
    """SL line style."""
    return {"color": (255, 59, 59), "thickness": 4}


def style_target() -> dict[str, Any]:
    """Target line/arrow style."""
    return {"color": (59, 130, 246), "thickness": 4}


def style_ob_zone() -> dict[str, Any]:
    """OB zone fill/glow style."""
    return {"fill": (0, 255, 136, 64), "glow": True}


def enforce_rules(content: dict) -> dict:
    """Ensure psychology and trade always present (viral validator)."""
    if "psychology" in content and not content.get("psychology"):
        content["psychology"] = pick(PSYCHOLOGY_LINES)
    if "trade" in content and not content.get("trade"):
        content["trade"] = pick(TRADE_LINES)
    return content


# Subtle footer (smaller brand strip)
FOOTER_H_SUBTLE = 52
CHART_STRIP_H   = 96   # optional mini “terminal” strip at bottom of text posts

# Viral hooks (title-bar subtitles + hooks) — aggressive curiosity / fear
_AGGRESSIVE_HOOKS = [
    "SMART MONEY ENTRY 👀",
    "MOST TRADERS FAIL HERE ⚠️",
    "DON'T MISS THIS SETUP",
    "HIGH PROBABILITY ZONE 🔥",
    "RETAIL GETS TRAPPED HERE ⚡",
    "INSTITUTIONS LOVE THIS 🏦",
    "WYCKOFF IN ACTION 🎯",
    "THIS IS HOW SMART MONEY MOVES 🧠",
    "RISK:REWARD GOLD ZONE 💰",
]
_VIRAL_HOOKS = _AGGRESSIVE_HOOKS  # alias

_URGENCY_LINES = [
    "Don't enter early.",
    "Wait for confirmation.",
    "This is where retail gets trapped.",
    "No confirmation — no trade.",
    "Patience pays here.",
]

_HUMAN_CLOSERS = [
    "Wait for confirmation.",
    "Avoid early entries.",
    "Follow the structure.",
    "Let price come to your zone.",
    "No confirmation — no trade.",
    "Manage your SL first.",
]


# ── Font loader ───────────────────────────────────────────────────────────────
_WIN_FONT_DIR = Path("C:/Windows/Fonts")
_LINUX_FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype"),
]

_FONT_CANDIDATES = {
    "bold":    ["arialbd.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "regular": ["arial.ttf",   "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
}


def _find_font(style: str) -> Path | None:
    candidates = _FONT_CANDIDATES.get(style, _FONT_CANDIDATES["regular"])
    search_dirs = [_WIN_FONT_DIR] + _LINUX_FONT_DIRS
    for name in candidates:
        for d in search_dirs:
            p = d / name
            if p.exists():
                return p
    return None


def _load(style: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _find_font(style)
    if path:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    return ImageFont.load_default()


# Pre-load font sizes we'll use (keyed by (style, size))
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font(style: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    key = (style, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = _load(style, size)
    return _FONT_CACHE[key]


# ── Text utilities ────────────────────────────────────────────────────────────

def _strip_emoji_markers(text: str) -> str:
    """Remove Telegram markdown markers and common emoji used as headers."""
    text = re.sub(r"\*([^*]+)\*", r"\1", text)       # *bold* → bold
    text = re.sub(r"_([^_]+)_", r"\1", text)          # _italic_ → italic
    text = re.sub(r"\\([.!#\-])", r"\1", text)        # MarkdownV2 escapes
    return text.strip()


def _clean_bullet(line: str) -> str:
    """Normalise bullet markers to a plain string."""
    line = line.strip()
    line = re.sub(r"^[•\-\*]\s*", "", line)
    return line


def _text_height(draw: ImageDraw.Draw, text: str, font, max_width: int) -> int:
    """Return pixel height of wrapped text block."""
    lines = textwrap.wrap(text, width=_wrap_chars(font, max_width))
    if not lines:
        return 0
    _, _, _, lh = draw.textbbox((0, 0), "Ay", font=font)
    return len(lines) * (lh + 4)


def _wrap_chars(font, max_px: int) -> int:
    """Approximate character count that fits in max_px pixels."""
    try:
        _, _, w, _ = font.getbbox("A")
        chars = max(10, int(max_px / max(w, 1)))
    except Exception:
        chars = 40
    return chars


def _truncate_words(text: str, max_words: int = 10, ellipsis: str = "") -> str:
    """Trim to max_words for scan-friendly lines (viral templates)."""
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",.;:") + ellipsis


def _wrap_max_words_per_line(text: str, max_words: int = 10) -> list[str]:
    """Split into lines with at most ``max_words`` words each."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    i = 0
    while i < len(words):
        chunk = words[i : i + max_words]
        lines.append(" ".join(chunk))
        i += max_words
    return lines


def _pick_viral_hook() -> str:
    return _rand_module.choice(_VIRAL_HOOKS)


def _pick_aggressive_hook() -> str:
    """Subtitle / hook for strategy & diagram title bars."""
    return _rand_module.choice(_AGGRESSIVE_HOOKS)


def _pick_urgency_line() -> str:
    return _rand_module.choice(_URGENCY_LINES)


def _pick_micro_cta() -> str:
    return _rand_module.choice(_HUMAN_CLOSERS) if _HUMAN_CLOSERS else "Wait for confirmation."


def _viralize_strategy_title(title: str) -> str:
    """Short powerful titles: RETEST → ENTRY, trim noise."""
    t = _strip_emoji_markers(title).strip()
    t = re.sub(r"(?i)\bretest\b", "ENTRY", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.upper() if t else "STRATEGY"


_EMOJI_NUM = ("1️⃣", "2️⃣", "3️⃣")


def _draw_rounded_shadow_card(
    draw: ImageDraw.Draw,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int],
    radius: int = 16,
    shadow_offset: tuple[int, int] = (4, 4),
    shadow_rgb: tuple[int, int, int] = (210, 215, 225),
) -> None:
    """Rounded rectangle with soft offset shadow (depth)."""
    l, t, r, b = box
    ox, oy = shadow_offset
    draw.rounded_rectangle(
        [l + ox, t + oy, r + ox, b + oy],
        radius=radius,
        fill=shadow_rgb,
    )
    draw.rounded_rectangle([l, t, r, b], radius=radius, fill=fill)


def _risk_label_trader_tone(risk_raw: str) -> str:
    """Turn generic risk labels into short action tone."""
    s = str(risk_raw).lower()
    if "high" in s:
        return "Higher risk — tighten SL"
    if "medium" in s:
        return "Moderate risk — manage SL"
    if "low" in s:
        return "Lower risk — still use SL"
    return "Moderate risk — manage SL"


def _draw_text_block(
    draw: ImageDraw.Draw,
    text: str,
    x: int,
    y: int,
    font,
    colour: tuple,
    max_width: int,
    line_spacing: int = 6,
) -> int:
    """Draw wrapped text starting at (x, y). Returns new y after last line."""
    wrap_at = _wrap_chars(font, max_width)
    lines = textwrap.wrap(text, width=wrap_at) or [""]
    for line in lines:
        draw.text((x, y), line, font=font, fill=colour)
        try:
            _, _, _, lh = draw.textbbox((0, 0), line, font=font)
        except Exception:
            lh = font.size if hasattr(font, "size") else 20
        y += lh + line_spacing
    return y


def _draw_bullet_list(
    draw: ImageDraw.Draw,
    items: list[str],
    x: int,
    y: int,
    font,
    max_width: int,
    bullet_colour: tuple = C_BULLET,
    text_colour: tuple = C_BODY,
    line_spacing: int = 8,
) -> int:
    """Draw a bullet list. Returns new y after last item."""
    dot_r   = 5
    dot_x   = x + 8
    text_x  = x + 22
    text_w  = max_width - 22

    try:
        _, _, _, lh = draw.textbbox((0, 0), "Ay", font=font)
    except Exception:
        lh = font.size if hasattr(font, "size") else 18

    for item in items:
        item = item.strip()
        if not item:
            continue
        # Draw bullet dot centred on text baseline
        draw.ellipse(
            [dot_x - dot_r, y + lh // 2 - dot_r,
             dot_x + dot_r, y + lh // 2 + dot_r],
            fill=bullet_colour,
        )
        y = _draw_text_block(draw, item, text_x, y, font, text_colour, text_w, line_spacing)
        y += 4  # gap between items
    return y


def _centre_text(draw: ImageDraw.Draw, text: str, y: int, font, colour: tuple, width: int = W) -> int:
    """Draw horizontally centred text. Returns y after."""
    try:
        _, _, tw, th = draw.textbbox((0, 0), text, font=font)
    except Exception:
        tw, th = width // 2, 20
    x = (width - tw) // 2
    draw.text((x, y), text, font=font, fill=colour)
    return y + th


def _draw_divider(draw: ImageDraw.Draw, y: int, left: int = MARGIN, right: int = W - MARGIN) -> int:
    draw.line([(left, y), (right, y)], fill=C_DIVIDER, width=1)
    return y + 1


def _draw_section_label(draw: ImageDraw.Draw, label: str, y: int) -> int:
    """Draw a blue section label (e.g. '🧠 CONCEPT'). Returns new y."""
    f = _font("bold", 28)
    draw.text((MARGIN, y), label, font=f, fill=C_SECTION_LBL)
    try:
        _, _, _, lh = draw.textbbox((0, 0), label, font=f)
    except Exception:
        lh = 28
    return y + lh + 6


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4 — CONTENT PARSER
# Extracts structured sections from the raw post content string
# ═══════════════════════════════════════════════════════════════════════════════

_SECTION_PATTERNS = [
    ("concept",    r"(?:🧠|Concept)[^\n]*\n(.*?)(?=\n[📍🛑🎯⚖️📌]|\Z)"),
    ("entry",      r"(?:📍|Entry)[^\n]*\n(.*?)(?=\n[🛑🎯⚖️📌]|\Z)"),
    ("stop_loss",  r"(?:🛑|Stop)[^\n]*\n(.*?)(?=\n[🎯⚖️📌]|\Z)"),
    ("target",     r"(?:🎯|Target|Profit)[^\n]*\n(.*?)(?=\n[📈⚖️📌]|\Z)"),
    ("best_market",r"(?:📈|Best Market|Ideal|When)[^\n]*\n(.*?)(?=\n[⚖️📌]|\Z)"),
    ("risk",       r"(?:⚖️|Risk Level)[^\n]*:\s*(.+)"),
    ("closer",     r"(?:📌)\s*(.+)"),
]


def parse_strategy_content(content: str) -> dict[str, Any]:
    """
    Parse a strategy post content string into structured sections.
    Each section is either a plain string or a list of strings (bullets).
    """
    clean = _strip_emoji_markers(content)
    result: dict[str, Any] = {}

    for key, pattern in _SECTION_PATTERNS:
        m = re.search(pattern, clean, re.DOTALL | re.IGNORECASE)
        if m:
            block = m.group(1).strip()
            lines = [l.strip() for l in block.splitlines() if l.strip()]
            # Detect bullet list
            if any(re.match(r"^[•\-\*]", l) for l in lines):
                result[key] = [_clean_bullet(l) for l in lines if l]
            elif len(lines) > 1:
                result[key] = " ".join(lines)
            else:
                result[key] = lines[0] if lines else ""

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED CANVAS HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

import random as _rand_module

def _new_canvas() -> tuple[Image.Image, ImageDraw.Draw]:
    img  = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    return img, draw


def _draw_title_bar(draw: ImageDraw.Draw, title: str, subtitle: str = "", *, compact: bool = False) -> int:
    """
    Solid blue title bar at the top.
    Returns the y position immediately below the bar.
    """
    if compact:
        bar_h = 130 if subtitle else 115
    else:
        bar_h = 190 if subtitle else 160
    draw.rectangle([(0, 0), (W, bar_h)], fill=C_TITLE_BAR)

    # Optional category pill
    if subtitle:
        pill_font = _font("bold", 24)
        pill_text = subtitle.upper()
        try:
            _, _, pw, ph = draw.textbbox((0, 0), pill_text, font=pill_font)
        except Exception:
            pw, ph = 120, 24
        pill_x = (W - pw) // 2
        pill_y = 24
        draw.rounded_rectangle(
            [pill_x - 16, pill_y - 6, pill_x + pw + 16, pill_y + ph + 6],
            radius=14,
            fill=(255, 255, 255),
        )
        draw.text((pill_x, pill_y), pill_text, font=pill_font, fill=C_ACCENT)
        title_y = pill_y + ph + 18
    else:
        title_y = 36

    # Main title — auto-size to fit
    for size in (72, 60, 50, 42, 36):
        f = _font("bold", size)
        try:
            _, _, tw, th = draw.textbbox((0, 0), title.upper(), font=f)
        except Exception:
            tw, th = W, size
        if tw <= INNER_W:
            _centre_text(draw, title.upper(), title_y, f, C_TITLE_TXT)
            title_y += th
            break

    return bar_h + 8


def _draw_viral_title_bar(draw: ImageDraw.Draw, title: str, subtitle: str = "") -> int:
    """
    Aggressive hook (subtitle) or random hook, then main title. No weak labels.
    """
    bar_h = 205
    draw.rectangle([(0, 0), (W, bar_h)], fill=C_TITLE_BAR)

    hook_text = subtitle.strip() if subtitle and subtitle.strip() else _pick_aggressive_hook()

    f_hook = _font("bold", 50)
    try:
        _, _, hw, hh = draw.textbbox((0, 0), hook_text, font=f_hook)
    except Exception:
        hw, hh = 400, 50
    if hw > INNER_W:
        f_hook = _font("bold", 38)
        try:
            _, _, hw, hh = draw.textbbox((0, 0), hook_text, font=f_hook)
        except Exception:
            hw, hh = INNER_W, 38
    draw.text(((W - hw) // 2, 10), hook_text, font=f_hook, fill=(255, 255, 255))

    y = 12 + hh + 12

    for size in (68, 56, 48, 40, 34):
        f = _font("bold", size)
        try:
            _, _, tw, th = draw.textbbox((0, 0), title.upper(), font=f)
        except Exception:
            tw, th = W, size
        if tw <= INNER_W:
            _centre_text(draw, title.upper(), y, f, C_TITLE_TXT)
            break

    return bar_h + 8


def _draw_human_closer(draw: ImageDraw.Draw, y: int) -> None:
    """Draw a human-touch closer line just above the footer area."""
    closer = _rand_module.choice(_HUMAN_CLOSERS) if "_HUMAN_CLOSERS" in dir() else "💡 Risk management is key."
    import sys
    _mod = sys.modules.get("content_engine.services.image_generator")
    if _mod and hasattr(_mod, "_HUMAN_CLOSERS"):
        closer = _rand_module.choice(_mod._HUMAN_CLOSERS)
    f = _font("bold", 26)
    try:
        _, _, tw, _ = draw.textbbox((0, 0), closer, font=f)
    except Exception:
        tw = 400
    draw.text(((W - tw) // 2, y), closer, font=f, fill=C_ACCENT)


def _draw_brand_footer(
    img: Image.Image,
    draw: ImageDraw.Draw,
    *,
    footer_bg: tuple[int, int, int] | None = None,
    footer_fg: tuple[int, int, int] | None = None,
    brand: str = "StocksWithGaurav",
    bar_height: int = 72,
) -> None:
    """Brand footer: centered text + logo. ``bar_height=FOOTER_H_SUBTLE`` for a smaller, subtle strip."""
    bg = footer_bg if footer_bg is not None else C_BRAND_BG
    fg = footer_fg if footer_fg is not None else C_BRAND_TXT
    bar_height = max(44, min(bar_height, 96))
    bar_top = H - bar_height
    draw.rectangle([(0, bar_top), (W, H)], fill=bg)

    fs = 24 if bar_height <= FOOTER_H_SUBTLE else 32
    f = _font("bold", fs)
    try:
        _, _, tw, th = draw.textbbox((0, 0), brand, font=f)
    except Exception:
        tw, th = 200, fs
    ty = bar_top + (bar_height - th) // 2
    draw.text(((W - tw) // 2, ty), brand, font=f, fill=fg)
    _paste_logo_footer(img, bar_top, bar_height)


def _draw_footer(draw: ImageDraw.Draw, brand: str = "StocksWithGaurav") -> None:
    """Backward-compatible footer; pastes logo when the draw target image is available."""
    base = getattr(draw, "im", None) or getattr(draw, "_image", None)
    if base is not None:
        _draw_brand_footer(base, draw, brand=brand)
        return
    bar_top = H - 72
    draw.rectangle([(0, bar_top), (W, H)], fill=C_BRAND_BG)
    f = _font("bold", 32)
    try:
        _, _, tw, _ = draw.textbbox((0, 0), brand, font=f)
    except Exception:
        tw = 200
    draw.text(((W - tw) // 2, bar_top + 18), brand, font=f, fill=C_BRAND_TXT)


def _apply_chart_texture_background(
    img: Image.Image,
    y0: int,
    y1: int,
    *,
    mode: str = "light",
    base_rgb: tuple[int, int, int] | None = None,
    with_grid: bool = True,
) -> None:
    """
    Subtle procedural chart-style texture in a horizontal band: vertical gradient,
    optional fine grid + faint candlesticks (``with_grid=False`` = clean gradient only).
    """
    if y1 <= y0:
        return
    y0 = max(0, min(y0, H))
    y1 = max(0, min(y1, H))
    if y1 <= y0:
        return

    br, bg, bb = base_rgb if base_rgb is not None else ((255, 255, 255) if mode == "light" else (12, 14, 22))
    is_dark = mode == "dark" or (br + bg + bb) < 380

    if is_dark:
        top = (max(br - 4, 0), max(bg - 4, 0), max(bb - 4, 0))
        bot = (min(br + 14, 255), min(bg + 14, 255), min(bb + 22, 255))
        grid = (min(br + 38, 255), min(bg + 42, 255), min(bb + 58, 255))
        up_body = (min(br + 55, 255), min(bg + 95, 255), min(bb + 75, 255))
        dn_body = (min(br + 110, 255), min(bg + 55, 255), min(bb + 55, 255))
        wick = (min(br + 70, 255), min(bg + 75, 255), min(bb + 95, 255))
    else:
        top = (min(255, br + 2), min(255, bg + 2), min(255, bb + 4))
        bot = (max(245, br - 8), max(248, bg - 6), min(255, bb + 8))
        grid = (max(210, br - 35), max(218, bg - 30), min(255, bb + 12))
        up_body = (175, 205, 185)
        dn_body = (245, 210, 210)
        wick = (200, 205, 215)

    draw = ImageDraw.Draw(img)
    band_h = y1 - y0
    for i in range(band_h):
        t = i / max(band_h, 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        yy = y0 + i
        draw.line([(0, yy), (W, yy)], fill=(r, g, b))

    if not with_grid:
        return

    for x in range(MARGIN, W - MARGIN, 48):
        draw.line([(x, y0), (x, y1)], fill=grid, width=1)
    for yy in range(y0, y1, 40):
        draw.line([(MARGIN, yy), (W - MARGIN, yy)], fill=grid, width=1)

    row_y = min(y1 - 12, y1 - 8)
    cx = MARGIN + 20
    for k in range(16):
        if cx > W - MARGIN - 30:
            break
        is_up = (k % 3) != 1
        body_w = 7
        body_h = 12 + (k % 5) * 5
        wick_top = row_y - body_h - 8
        cx_mid = cx + body_w // 2
        c_body = up_body if is_up else dn_body
        draw.line([(cx_mid, wick_top), (cx_mid, row_y)], fill=wick, width=1)
        draw.rectangle([cx, row_y - body_h, cx + body_w, row_y], outline=c_body, fill=c_body)
        cx += 20


def _save(img: Image.Image, prefix: str) -> str:
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}_{ts}.png"
    path = str(_OUT_DIR / name)
    print(f"[DEBUG] Saving image to: {path}")
    img.save(path, "PNG", optimize=True)
    log.info("Image saved: %s", path)
    print("[DEBUG] Image saved successfully")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM ENGINE — visual price-action illustrations
# ═══════════════════════════════════════════════════════════════════════════════

# Diagram canvas sits between title bar and the stats footer strip.
# All coordinates are relative to the diagram bounding box passed in.

# ── Diagram colour constants ──────────────────────────────────────────────────
DC_PRICE      = (30,  30,  30)      # price line colour
DC_BULL       = (22, 160,  60)      # bullish arrows, zones
DC_BEAR       = (210,  40,  40)     # bearish arrows, zones
DC_OB         = (15,  80, 180)      # Order Block / key zone (blue)
DC_FVG        = (255, 165,   0)     # Fair Value Gap (amber)
DC_SUPPLY     = (210,  40,  40, 60) # supply zone fill (semi-transparent red)
DC_DEMAND     = (22,  160,  60, 60) # demand zone fill (semi-transparent green)
DC_VWAP       = (130,  60, 200)     # VWAP line (purple)
DC_EMA        = (255, 140,   0)     # EMA line (orange)
DC_RANGE_BOX  = (200, 200, 200)     # consolidation box border
DC_GRID       = (240, 240, 240)     # subtle grid lines
DC_LABEL      = (80,  80,  80)      # annotation text colour
DC_LABEL_BULL = (15, 120,  50)      # bullish label colour
DC_LABEL_BEAR = (180,  30,  30)     # bearish label colour


# ── Low-level drawing primitives ──────────────────────────────────────────────

def _polyline(draw: ImageDraw.Draw, pts: list[tuple[int, int]], colour, width: int = 3) -> None:
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=colour, width=width)


def _filled_rect(img: Image.Image, box: tuple, colour_rgba: tuple) -> None:
    """Alpha-blended filled rectangle for zone shading."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.rectangle(box, fill=colour_rgba)
    base = img.convert("RGBA")
    merged = Image.alpha_composite(base, overlay)
    img.paste(merged.convert("RGB"))


def _glow_zone(img: Image.Image, box: tuple[int, int, int, int], rgb: tuple[int, int, int], layers: int = 5) -> None:
    """Soft glow around a zone (live-trade emphasis)."""
    l, t, r, b = box
    for i in range(layers, 0, -1):
        pad = i * 5
        a = min(28 + i * 16, 130)
        _filled_rect(img, (l - pad, t - pad, r + pad, b + pad), (*rgb, a))


def _solid_hline(draw: ImageDraw.Draw, y: int, x0: int, x1: int, colour: tuple, width: int = 4) -> None:
    draw.line([(x0, y), (x1, y)], fill=colour, width=width)


def _arrow(
    draw: ImageDraw.Draw,
    x0: int, y0: int,
    x1: int, y1: int,
    colour,
    width: int = 4,
    head: int = 18,
) -> None:
    """Draw a line with a filled arrowhead at (x1, y1)."""
    import math
    draw.line([(x0, y0), (x1, y1)], fill=colour, width=width)
    angle = math.atan2(y1 - y0, x1 - x0)
    for side in (+0.4, -0.4):
        ax = x1 - head * math.cos(angle - side)
        ay = y1 - head * math.sin(angle - side)
        draw.polygon([(x1, y1), (int(ax), int(ay))], fill=colour)


def _label(
    draw: ImageDraw.Draw,
    text: str,
    x: int, y: int,
    colour=DC_LABEL,
    size: int = 26,
    anchor: str = "left",   # left | right | centre
    bg: tuple | None = None,
) -> None:
    f = _font("bold", size)
    try:
        _, _, tw, th = draw.textbbox((0, 0), text, font=f)
    except Exception:
        tw, th = len(text) * size // 2, size
    if anchor == "right":
        x -= tw
    elif anchor == "centre":
        x -= tw // 2
    if bg:
        pad = 6
        draw.rounded_rectangle([x - pad, y - 2, x + tw + pad, y + th + 2], radius=6, fill=bg)
    draw.text((x, y), text, font=f, fill=colour)


def _horizontal_dashed(
    draw: ImageDraw.Draw,
    y: int, x0: int, x1: int,
    colour=DC_LABEL, dash: int = 12, gap: int = 8,
) -> None:
    x = x0
    on = True
    while x < x1:
        end = min(x + (dash if on else gap), x1)
        if on:
            draw.line([(x, y), (end, y)], fill=colour, width=2)
        x = end
        on = not on


def _candle(
    draw: ImageDraw.Draw,
    cx: int, top: int, bottom: int, body_top: int, body_bot: int,
    bullish: bool = True,
) -> None:
    """Draw a single candlestick."""
    colour = DC_BULL if bullish else DC_BEAR
    # Wick
    draw.line([(cx, top), (cx, bottom)], fill=DC_PRICE, width=2)
    # Body
    w = 16
    draw.rectangle([cx - w, body_top, cx + w, body_bot], fill=colour, outline=colour)


# ── Diagram bounding box helper ───────────────────────────────────────────────

def _diagram_box(title_bar_bottom: int, footer_top: int, info_strip_h: int = 130) -> tuple:
    """
    Return (left, top, right, bottom, width, height) of usable diagram area.
    info_strip_h: pixels reserved at bottom of diagram for Entry/SL/Target text.
    """
    pad = 40
    left   = MARGIN - 20
    right  = W - MARGIN + 20
    top    = title_bar_bottom + pad
    bottom = footer_top - info_strip_h - pad
    return left, top, right, bottom, right - left, bottom - top


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM TEMPLATES — one function per strategy
# Each function receives (img, draw, box) and draws inside the box.
# ═══════════════════════════════════════════════════════════════════════════════

def _diag_order_block(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Live trade vibe: glowing OB, thick entry/SL/target, momentum arrows.
    Uses style_entry/sl/target from viral engine.
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2
    G_ENTRY = style_entry()["color"]
    G_SL = style_sl()["color"]
    G_TP = style_target()["color"]

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    pts = [
        (l + 30,  mid_y + 60),
        (l + 110, mid_y + 20),
        (l + 190, mid_y + 50),
        (l + 270, mid_y + 10),
        (l + 310, mid_y - 120),
        (l + 380, mid_y - 60),
        (l + 420, mid_y - 80),
        (l + 500, mid_y - 220),
        (l + bw - 30, mid_y - 240),
    ]
    _polyline(draw, pts, DC_PRICE, width=4)
    # Momentum arrows along impulse legs
    _arrow(draw, l + 300, mid_y - 100, l + 360, mid_y - 130, G_ENTRY, width=5, head=20)
    _arrow(draw, l + 440, mid_y - 100, l + 520, mid_y - 200, G_ENTRY, width=6, head=22)

    ob_left, ob_right = l + 240, l + 320
    ob_top, ob_bot = mid_y - 20, mid_y + 80
    _glow_zone(img, (ob_left, ob_top, ob_right, ob_bot), (40, 220, 100))
    _filled_rect(img, (ob_left, ob_top, ob_right, ob_bot), (30, 200, 90, 120))
    draw.rectangle([ob_left, ob_top, ob_right, ob_bot], outline=(0, 160, 80), width=4)
    _label(draw, "OB — INSTITUTION ZONE", ob_left + 4, ob_top - 36, colour=(0, 120, 60), size=22)

    bos_x = l + 315
    draw.line([(bos_x, t + 20), (bos_x, ob_top)], fill=DC_OB, width=2)
    _label(draw, "BOS", bos_x + 6, t + 18, colour=DC_OB, size=24)

    _arrow(draw, l + 510, mid_y - 48, l + 418, mid_y - 78, G_ENTRY, width=7, head=22)
    _label(draw, "RETEST → ENTRY", l + 400, mid_y - 95, colour=G_ENTRY, size=22, bg=(230, 255, 235))

    _arrow(draw, l + 425, mid_y - 108, l + 505, mid_y - 215, G_ENTRY, width=8, head=24)
    _label(draw, "MOMENTUM ↑", l + 430, mid_y - 228, colour=G_TP, size=22)

    entry_y = mid_y - 75
    sl_y = ob_bot
    tp_y = mid_y - 220
    _solid_hline(draw, entry_y, l + 360, r - 28, G_ENTRY, width=5)
    _solid_hline(draw, sl_y, l + 360, r - 28, G_SL, width=6)
    _solid_hline(draw, tp_y, l + 360, r - 28, G_TP, width=5)
    _arrow(draw, r - 120, tp_y - 2, r - 45, tp_y - 55, G_TP, width=5, head=18)

    _label(draw, "RETEST → CONFIRM → ENTER", r - 28, entry_y - 32, colour=G_ENTRY, size=20, anchor="right", bg=(220, 255, 225))
    _label(draw, "SL: STRICT", r - 28, sl_y + 6, colour=G_SL, size=20, anchor="right", bg=(255, 230, 230))
    _label(draw, "TARGET: LIQUIDITY (2–3R)", r - 28, tp_y - 34, colour=G_TP, size=20, anchor="right", bg=(230, 240, 255))


def _diag_fvg(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    3-candle imbalance → FVG zone → price fills gap → continuation
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # 3 candles showing the FVG formation
    c1x, c2x, c3x = l + 80, l + 150, l + 220
    # Candle 1 — bullish
    _candle(draw, c1x, mid_y + 20, mid_y + 90, mid_y + 30, mid_y + 80, bullish=True)
    # Candle 2 — big bullish impulse (creates the gap)
    _candle(draw, c2x, mid_y - 120, mid_y + 20, mid_y - 110, mid_y + 10, bullish=True)
    # Candle 3 — continuation (does NOT reach back to c1 wick)
    _candle(draw, c3x, mid_y - 170, mid_y - 60, mid_y - 160, mid_y - 70, bullish=True)

    # FVG zone (between c1 wick high and c3 wick low)
    fvg_top  = mid_y - 60
    fvg_bot  = mid_y + 20
    _filled_rect(img, (c1x + 20, fvg_top, c3x + 30, fvg_bot), (255, 165, 0, 55))
    draw.rectangle([c1x + 20, fvg_top, c3x + 30, fvg_bot], outline=DC_FVG, width=2)
    _label(draw, "DISPLACEMENT MOVE", c1x - 10, fvg_top - 70, colour=DC_FVG, size=22)
    _label(draw, "FVG = IMBALANCE ZONE", c1x + 24, fvg_top - 30, colour=DC_FVG, size=24)

    # Price path continuing right, then dipping into FVG, then rallying
    pts = [
        (c3x + 30, mid_y - 100),
        (l + 360,  mid_y - 130),
        (l + 430,  mid_y - 20),   # fill dip into FVG
        (l + 490,  mid_y - 40),   # rejection
        (l + 560,  mid_y - 200),
        (l + bw - 30, mid_y - 250),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Fill arrow
    _arrow(draw, l + 380, mid_y - 100, l + 430, mid_y - 25, DC_BEAR, width=3, head=14)
    _label(draw, "GAP FILL", l + 385, mid_y - 100, colour=DC_LABEL_BEAR, size=22)

    # Continuation arrow
    _arrow(draw, l + 495, mid_y - 60, l + 555, mid_y - 195, DC_BULL, width=4, head=18)
    _label(draw, "CONTINUATION", l + 440, mid_y - 230, colour=DC_LABEL_BULL, size=22)

    # Entry / SL / Target — viral style
    entry_y = mid_y - 35
    sl_y    = fvg_bot + 10
    tp_y    = mid_y - 240
    G_ENTRY = style_entry()["color"]
    G_SL = style_sl()["color"]
    G_TP = style_target()["color"]
    _solid_hline(draw, entry_y, l + 455, r - 30, G_ENTRY, width=4)
    _solid_hline(draw, sl_y,    l + 455, r - 30, G_SL, width=4)
    _solid_hline(draw, tp_y,    l + 455, r - 30, G_TP, width=4)
    _label(draw, "WAIT -> CONFIRM -> ENTER", r - 30, entry_y - 28, colour=G_ENTRY, size=20, anchor="right", bg=(220, 255, 225))
    _label(draw, "SL BELOW GAP", r - 30, sl_y + 4, colour=G_SL, size=20, anchor="right", bg=(255, 230, 230))
    _label(draw, "TARGET: NEXT LIQUIDITY (2-3R)", r - 30, tp_y - 30, colour=G_TP, size=20, anchor="right", bg=(230, 240, 255))


def _diag_liquidity_grab(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Equal highs → stop-hunt wick through them → sharp reversal
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # Price forming equal highs (retail stops cluster here)
    eq_y = mid_y - 80
    pts_left = [
        (l + 30,  mid_y + 100),
        (l + 110, eq_y),
        (l + 190, mid_y + 40),
        (l + 280, eq_y + 5),
        (l + 360, mid_y + 30),
        (l + 440, eq_y - 2),     # third equal high
    ]
    _polyline(draw, pts_left, DC_PRICE, width=3)

    # Equal-high dashed line
    _horizontal_dashed(draw, eq_y, l + 80, l + 480, colour=(180, 120, 0))
    _label(draw, "EQUAL HIGHS (Retail Stops)", l + 80, eq_y - 30, colour=(150, 100, 0), size=22)

    # Stop-hunt wick — spike above, then close back below
    hunt_x = l + 520
    _arrow(draw, hunt_x, eq_y + 20, hunt_x, t + 20, DC_BEAR, width=3, head=16)
    _label(draw, "STOP HUNT", hunt_x + 10, t + 22, colour=DC_LABEL_BEAR, size=22)

    # Reversal price path
    pts_right = [
        (l + 440, eq_y - 2),
        (hunt_x,  t + 30),       # spike
        (hunt_x + 20, eq_y + 40),# close back below equal highs
        (l + 580, mid_y + 80),
        (l + 650, mid_y + 200),
        (l + bw - 30, mid_y + 230),
    ]
    _polyline(draw, pts_right, DC_PRICE, width=3)

    # Reversal arrow
    _arrow(draw, hunt_x + 30, eq_y + 60, l + 600, mid_y + 180, DC_BULL, width=4, head=18)
    _label(draw, "REVERSAL", hunt_x + 35, eq_y + 55, colour=DC_LABEL_BULL, size=22)

    # Entry / SL
    entry_y = eq_y + 30
    sl_y    = t + 25
    tp_y    = mid_y + 230
    _horizontal_dashed(draw, entry_y, hunt_x + 20, r - 30, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    hunt_x,       r - 30, colour=DC_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y - 28, colour=DC_LABEL_BULL, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y + 4,     colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_breaker_block(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    OB formed → OB broken (BOS) → broken OB becomes supply → price retests & rejects
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # Original OB zone
    ob_top = mid_y - 40
    ob_bot = mid_y + 60
    ob_l   = l + 60
    ob_r   = l + 160
    _filled_rect(img, (ob_l, ob_top, ob_r, ob_bot), (22, 160, 60, 40))
    draw.rectangle([ob_l, ob_top, ob_r, ob_bot], outline=DC_OB, width=2)
    _label(draw, "OB (Demand)", ob_l, ob_top - 30, colour=DC_OB, size=22)

    # Price path: respect OB twice, then BOS down through it
    pts = [
        (l + 30,  mid_y + 120),
        (l + 90,  ob_bot - 10),   # touch OB → bounce
        (l + 160, mid_y + 150),
        (l + 220, ob_bot - 5),    # second touch → weaker bounce
        (l + 290, mid_y + 10),
        (l + 370, ob_top + 100),  # BOS — closes below OB bottom
        (l + 430, mid_y - 30),    # pull-back to former OB (now Breaker)
        (l + 480, mid_y + 20),    # rejection
        (l + 560, mid_y + 180),
        (l + bw - 30, mid_y + 220),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Breaker Block zone (OB flipped to supply)
    bb_top = ob_top
    bb_bot = ob_bot
    bb_l   = l + 390
    bb_r   = l + 490
    _filled_rect(img, (bb_l, bb_top, bb_r, bb_bot), (210, 40, 40, 50))
    draw.rectangle([bb_l, bb_top, bb_r, bb_bot], outline=DC_BEAR, width=2)
    _label(draw, "BREAKER (Supply)", bb_l, bb_top - 30, colour=DC_LABEL_BEAR, size=22)

    # BOS label
    _label(draw, "BOS", l + 340, mid_y + 30, colour=DC_LABEL_BEAR, size=24,
           bg=(255, 220, 220))

    # Rejection arrow
    _arrow(draw, l + 480, mid_y + 10, l + 560, mid_y + 170, DC_BEAR, width=4, head=18)
    _label(draw, "REJECTION", l + 490, mid_y - 10, colour=DC_LABEL_BEAR, size=22)

    # Entry / SL
    entry_y = ob_top + 5
    sl_y    = ob_top - 30
    tp_y    = mid_y + 220
    _horizontal_dashed(draw, entry_y, bb_r, r - 30, colour=DC_BEAR)
    _horizontal_dashed(draw, sl_y,    bb_r, r - 30, colour=DC_LABEL_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y + 4,  colour=DC_LABEL_BEAR, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y - 28,    colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_trend_pullback(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Uptrend → EMA line → pullback to EMA → bounce → continuation
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # EMA line (gently sloping up)
    ema_pts = [(l + x, mid_y + 120 - int(x * 0.30)) for x in range(0, bw, 20)]
    _polyline(draw, ema_pts, DC_EMA, width=2)
    _label(draw, "20 EMA", ema_pts[-1][0] - 30, ema_pts[-1][1] - 32, colour=DC_EMA, size=22)

    # Price path: uptrend with 2 pullbacks to EMA
    pts = [
        (l + 30,  mid_y + 100),
        (l + 110, mid_y - 20),
        (l + 170, mid_y + 50),    # pullback 1 → touches EMA
        (l + 240, mid_y - 80),
        (l + 310, mid_y - 10),    # pullback 2 → touches EMA
        (l + 380, mid_y - 130),
        (l + 460, mid_y - 60),    # pullback 3 (entry zone)
        (l + 530, mid_y - 200),
        (l + bw - 30, mid_y - 240),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Higher-highs & higher-lows labels
    _label(draw, "HH", l + 100, mid_y - 58, colour=DC_LABEL_BULL, size=22)
    _label(draw, "HH", l + 230, mid_y - 108, colour=DC_LABEL_BULL, size=22)
    _label(draw, "HL", l + 150, mid_y + 28, colour=DC_LABEL_BULL, size=22)
    _label(draw, "HL", l + 290, mid_y - 32, colour=DC_LABEL_BULL, size=22)
    _label(draw, "HL", l + 440, mid_y - 82, colour=DC_LABEL_BULL, size=22)

    # Entry arrow at 3rd pullback
    _arrow(draw, l + 460, mid_y - 60, l + 530, mid_y - 195, DC_BULL, width=4, head=18)
    _label(draw, "ENTRY", l + 470, mid_y - 55, colour=DC_LABEL_BULL, size=22,
           bg=(220, 255, 220))

    # SL below ema level at entry
    entry_y = mid_y - 65
    sl_y    = mid_y + 10
    _horizontal_dashed(draw, entry_y, l + 460, r - 30, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    l + 460, r - 30, colour=DC_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y - 28, colour=DC_LABEL_BULL, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y + 4,     colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_range_breakout(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Horizontal range → volume breakout → measured move projection
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    range_top = mid_y - 80
    range_bot = mid_y + 80
    range_end = l + 430

    # Ranging price (zig-zag inside box)
    pts = [
        (l + 30,  mid_y + 20),
        (l + 100, range_top + 10),
        (l + 170, range_bot - 10),
        (l + 250, range_top + 15),
        (l + 330, range_bot - 15),
        (l + 400, range_top + 5),
        (range_end, mid_y),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Range box
    _filled_rect(img, (l + 30, range_top, range_end, range_bot), (200, 200, 255, 30))
    draw.rectangle([l + 30, range_top, range_end, range_bot], outline=DC_RANGE_BOX, width=2)
    _label(draw, "CONSOLIDATION ZONE", l + 50, range_top - 30, colour=(100, 100, 180), size=22)

    # Breakout candle
    bo_x = range_end + 30
    _candle(draw, bo_x, range_top - 60, range_top + 20, range_top - 50, range_top + 10, bullish=True)
    _label(draw, "BREAKOUT", bo_x - 40, range_top - 90, colour=DC_LABEL_BULL, size=22)

    # Continuation
    pts2 = [
        (range_end, mid_y),
        (bo_x + 20, range_top - 30),
        (l + 560, range_top - 120),
        (l + bw - 30, range_top - 160),
    ]
    _polyline(draw, pts2, DC_PRICE, width=3)

    # Measured-move projection bracket
    mm_top  = range_top - 160
    mm_bot  = range_top
    mm_x    = l + bw - 50
    draw.line([(mm_x, mm_bot), (mm_x, mm_top)], fill=DC_BULL, width=2)
    draw.line([(mm_x - 10, mm_bot), (mm_x + 10, mm_bot)], fill=DC_BULL, width=2)
    draw.line([(mm_x - 10, mm_top), (mm_x + 10, mm_top)], fill=DC_BULL, width=2)
    _label(draw, "TARGET", mm_x - 80, (mm_top + mm_bot) // 2 - 14,
           colour=DC_LABEL_BULL, size=22)

    entry_y = range_top - 10
    sl_y    = range_top + 25
    _horizontal_dashed(draw, entry_y, bo_x + 30, mm_x - 60, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    bo_x + 30, mm_x - 60, colour=DC_BEAR)
    _label(draw, "ENTRY", bo_x + 35, entry_y - 28, colour=DC_LABEL_BULL, size=22)
    _label(draw, "SL",    bo_x + 35, sl_y + 4,     colour=DC_LABEL_BEAR, size=22)


def _diag_vwap(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Price dips below VWAP → reclaims VWAP with momentum → rally
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # VWAP — slightly rising horizontal reference
    vwap_pts = [(l + x, mid_y + int(x * 0.04) - 30) for x in range(0, bw, 20)]
    _polyline(draw, vwap_pts, DC_VWAP, width=3)
    _label(draw, "VWAP", vwap_pts[-1][0] - 20, vwap_pts[-1][1] - 34, colour=DC_VWAP, size=24)

    # Price path: above VWAP → drops below → reclaims → rallies
    pts = [
        (l + 30,  mid_y - 60),
        (l + 120, mid_y - 30),    # approaching VWAP
        (l + 200, mid_y + 60),    # drops below VWAP
        (l + 270, mid_y + 90),    # low
        (l + 350, mid_y + 30),    # starting to recover
        (l + 410, mid_y - 10),    # reclaim VWAP candle
        (l + 480, mid_y - 80),
        (l + 560, mid_y - 170),
        (l + bw - 30, mid_y - 200),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Zone below VWAP (bearish area)
    vwap_at_200 = mid_y + int(200 * 0.04) - 30
    _filled_rect(img, (l + 150, vwap_at_200, l + 430, mid_y + 110), (255, 100, 100, 25))
    _label(draw, "Below VWAP", l + 200, mid_y + 65, colour=DC_LABEL_BEAR, size=21)

    # Reclaim annotation
    reclaim_x = l + 400
    reclaim_y = mid_y - 15
    _label(draw, "RECLAIM", reclaim_x - 30, reclaim_y - 38, colour=DC_VWAP, size=22,
           bg=(240, 220, 255))
    _arrow(draw, reclaim_x + 30, reclaim_y, l + 480, mid_y - 75, DC_BULL, width=4, head=18)

    # Entry / SL
    entry_y = mid_y - 10
    sl_y    = mid_y + 50
    _horizontal_dashed(draw, entry_y, reclaim_x, r - 30, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    reclaim_x, r - 30, colour=DC_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y - 28, colour=DC_LABEL_BULL, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y + 4,     colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_orb(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    15-min ORB candle → flat range → breakout above high → continuation
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    orb_high = mid_y - 70
    orb_low  = mid_y + 70
    orb_x    = l + 100

    # ORB candle (first 15 min)
    _candle(draw, orb_x, orb_high - 20, orb_low + 20, orb_high, orb_low, bullish=True)
    _label(draw, "ORB (09:15-09:30)", l + 30, orb_high - 52, colour=DC_OB, size=22)

    # ORB boundary lines
    draw.line([(orb_x + 25, orb_high), (l + 470, orb_high)], fill=DC_BULL, width=2)
    draw.line([(orb_x + 25, orb_low),  (l + 470, orb_low)],  fill=DC_BEAR, width=2)
    _label(draw, "ORB HIGH", l + 360, orb_high - 28, colour=DC_LABEL_BULL, size=22)
    _label(draw, "ORB LOW",  l + 360, orb_low + 6,   colour=DC_LABEL_BEAR, size=22)

    # Sideways chop inside the range
    pts_chop = [
        (orb_x + 30, mid_y + 20),
        (l + 200, orb_high + 20),
        (l + 270, orb_low - 20),
        (l + 350, orb_high + 10),
        (l + 430, mid_y + 10),
    ]
    _polyline(draw, pts_chop, DC_PRICE, width=3)

    # Breakout
    bo_x = l + 480
    pts_break = [
        (l + 430, mid_y + 10),
        (bo_x,    orb_high - 10),   # candle closes above ORB high
        (l + 560, orb_high - 90),
        (l + bw - 30, orb_high - 150),
    ]
    _polyline(draw, pts_break, DC_PRICE, width=3)

    _candle(draw, bo_x, orb_high - 30, mid_y + 15, orb_high - 20, mid_y + 5, bullish=True)
    _label(draw, "BREAKOUT", bo_x - 50, orb_high - 60, colour=DC_LABEL_BULL, size=22,
           bg=(220, 255, 220))
    _arrow(draw, bo_x + 30, orb_high - 20, l + 560, orb_high - 85, DC_BULL, width=4, head=18)

    entry_y = orb_high - 5
    sl_y    = mid_y
    _horizontal_dashed(draw, entry_y, bo_x + 30, r - 30, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    bo_x + 30, r - 30, colour=DC_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y - 28, colour=DC_LABEL_BULL, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y + 4,     colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_sr_flip(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Support tested multiple times → breakdown → retest as resistance → drop
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2
    sup_y = mid_y + 20

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # Support line
    draw.line([(l + 30, sup_y), (l + 420, sup_y)], fill=DC_BULL, width=2)
    _label(draw, "SUPPORT", l + 50, sup_y + 8, colour=DC_LABEL_BULL, size=22)

    # Price bouncing off support twice
    pts = [
        (l + 30,  mid_y - 100),
        (l + 110, sup_y + 5),     # touch 1
        (l + 190, mid_y - 60),
        (l + 280, sup_y + 3),     # touch 2
        (l + 360, mid_y - 30),
        (l + 430, sup_y - 5),     # weak bounce — breakdown imminent
        (l + 490, sup_y + 80),    # breaks below (BOS)
        (l + 540, sup_y + 40),    # pull-back to former support (now resistance)
        (l + 570, sup_y + 15),    # kisses underside
        (l + 630, sup_y + 130),
        (l + bw - 30, sup_y + 170),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Flip zone (resistance box)
    flip_l = l + 490
    flip_r = l + 590
    _filled_rect(img, (flip_l, sup_y - 30, flip_r, sup_y + 30), (210, 40, 40, 50))
    draw.rectangle([flip_l, sup_y - 30, flip_r, sup_y + 30], outline=DC_BEAR, width=2)
    _label(draw, "FLIP: now RESISTANCE", flip_l, sup_y - 58, colour=DC_LABEL_BEAR, size=22)

    # Breakdown arrow
    _arrow(draw, l + 570, sup_y + 20, l + 630, sup_y + 125, DC_BEAR, width=4, head=18)

    entry_y = sup_y + 12
    sl_y    = sup_y - 25
    _horizontal_dashed(draw, entry_y, flip_r, r - 30, colour=DC_BEAR)
    _horizontal_dashed(draw, sl_y,    flip_r, r - 30, colour=DC_LABEL_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y + 4,  colour=DC_LABEL_BEAR, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y - 28,    colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_trendline_break(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Rising trendline with 3 touches → break below → retest from underside → drop
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # Trendline (rising) connecting 3 swing lows
    tl_pts = [(l + 50, mid_y + 140), (l + 220, mid_y + 40), (l + 390, mid_y - 60)]
    _polyline(draw, tl_pts, DC_BULL, width=2)
    draw.line([(l + 50, mid_y + 140), (l + 500, mid_y - 110)], fill=DC_BULL, width=2)
    _label(draw, "TRENDLINE", l + 80, mid_y + 130, colour=DC_LABEL_BULL, size=22)

    # Price path touching trendline 3 times
    pts = [
        (l + 30,  mid_y - 60),
        (l + 80,  mid_y + 120),   # touch 1 (bounce)
        (l + 160, mid_y - 120),
        (l + 240, mid_y + 30),    # touch 2 (bounce)
        (l + 320, mid_y - 90),
        (l + 410, mid_y - 70),    # touch 3 (weaker bounce)
        (l + 470, mid_y - 130),
        (l + 510, mid_y - 40),    # break BELOW trendline
        (l + 550, mid_y - 90),    # retest from underside
        (l + 575, mid_y - 80),
        (l + 620, mid_y + 50),
        (l + bw - 30, mid_y + 100),
    ]
    _polyline(draw, pts, DC_PRICE, width=3)

    # Touch labels
    _label(draw, "1", l + 68, mid_y + 95, colour=DC_LABEL_BULL, size=22, bg=(220, 255, 220))
    _label(draw, "2", l + 228, mid_y + 5,  colour=DC_LABEL_BULL, size=22, bg=(220, 255, 220))
    _label(draw, "3", l + 398, mid_y - 95, colour=DC_LABEL_BULL, size=22, bg=(220, 255, 220))

    # Break label
    _label(draw, "BREAK", l + 495, mid_y - 30, colour=DC_LABEL_BEAR, size=22,
           bg=(255, 220, 220))

    # Retest and rejection
    _label(draw, "RETEST", l + 530, mid_y - 115, colour=DC_LABEL_BEAR, size=22)
    _arrow(draw, l + 580, mid_y - 75, l + 625, mid_y + 45, DC_BEAR, width=4, head=18)

    entry_y = mid_y - 78
    sl_y    = mid_y - 120
    _horizontal_dashed(draw, entry_y, l + 555, r - 30, colour=DC_BEAR)
    _horizontal_dashed(draw, sl_y,    l + 555, r - 30, colour=DC_LABEL_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y + 4,  colour=DC_LABEL_BEAR, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y - 28,    colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_consolidation(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    Strong impulse → tight flag consolidation → breakout in direction of impulse
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # Impulse move up
    imp_pts = [
        (l + 30, mid_y + 130),
        (l + 80, mid_y + 50),
        (l + 130, mid_y - 80),
        (l + 180, mid_y - 150),
    ]
    _polyline(draw, imp_pts, DC_PRICE, width=4)
    _label(draw, "IMPULSE", l + 40, mid_y + 100, colour=DC_LABEL_BULL, size=22)

    # Flag consolidation (slight counter-trend drift)
    flag_pts = [
        (l + 180, mid_y - 150),
        (l + 230, mid_y - 120),
        (l + 280, mid_y - 140),
        (l + 330, mid_y - 110),
        (l + 380, mid_y - 130),
        (l + 420, mid_y - 110),
    ]
    _polyline(draw, flag_pts, DC_PRICE, width=3)

    # Flag box
    flag_top = mid_y - 155
    flag_bot = mid_y - 100
    _filled_rect(img, (l + 175, flag_top, l + 435, flag_bot), (200, 200, 255, 35))
    draw.rectangle([l + 175, flag_top, l + 435, flag_bot], outline=(120, 120, 200), width=2)
    _label(draw, "FLAG", l + 280, flag_top - 30, colour=(100, 100, 180), size=22,
           anchor="centre")

    # Breakout continuation
    bo_pts = [
        (l + 420, mid_y - 110),
        (l + 470, mid_y - 190),
        (l + 540, mid_y - 250),
        (l + bw - 30, mid_y - 280),
    ]
    _polyline(draw, bo_pts, DC_PRICE, width=4)
    _arrow(draw, l + 435, mid_y - 130, l + 480, mid_y - 200, DC_BULL, width=4, head=18)
    _label(draw, "BREAKOUT", l + 430, flag_top - 60, colour=DC_LABEL_BULL, size=22)

    entry_y = flag_top - 5
    sl_y    = flag_bot + 10
    _horizontal_dashed(draw, entry_y, l + 435, r - 30, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    l + 435, r - 30, colour=DC_BEAR)
    _label(draw, "ENTRY", r - 30, entry_y - 28, colour=DC_LABEL_BULL, size=22, anchor="right")
    _label(draw, "SL",    r - 30, sl_y + 4,     colour=DC_LABEL_BEAR, size=22, anchor="right")


def _diag_momentum_scalp(img: Image.Image, draw: ImageDraw.Draw, box: tuple) -> None:
    """
    3-candle momentum burst on 3-min chart → small pullback entry → tight TP
    """
    l, t, r, b, bw, bh = box
    mid_y = t + bh // 2

    for gx in range(l, r, 80):
        draw.line([(gx, t), (gx, b)], fill=DC_GRID, width=1)

    # Pre-burst sideways
    pts_pre = [
        (l + 30, mid_y + 30),
        (l + 80, mid_y + 10),
        (l + 130, mid_y + 40),
        (l + 180, mid_y + 20),
    ]
    _polyline(draw, pts_pre, DC_PRICE, width=2)

    # 3 consecutive bullish momentum candles
    cx_list = [l + 220, l + 280, l + 340]
    tops    = [mid_y - 20, mid_y - 90, mid_y - 170]
    bots    = [mid_y + 40, mid_y - 10, mid_y - 80]
    for cx, ctop, cbot in zip(cx_list, tops, bots):
        _candle(draw, cx, ctop - 10, cbot + 10, ctop, cbot, bullish=True)

    _label(draw, "MOMENTUM BURST", l + 210, mid_y - 215, colour=DC_LABEL_BULL, size=22)

    # Small pullback (1-2 candles)
    pull_pts = [
        (l + 360, mid_y - 160),
        (l + 400, mid_y - 130),
        (l + 430, mid_y - 145),
    ]
    _polyline(draw, pull_pts, DC_PRICE, width=3)
    _label(draw, "PULLBACK", l + 370, mid_y - 120, colour=DC_LABEL_BEAR, size=21)

    # Entry and fast continuation
    cont_pts = [
        (l + 430, mid_y - 145),
        (l + 490, mid_y - 210),
        (l + 540, mid_y - 255),
    ]
    _polyline(draw, cont_pts, DC_PRICE, width=3)
    _arrow(draw, l + 430, mid_y - 150, l + 490, mid_y - 208, DC_BULL, width=4, head=18)
    _label(draw, "ENTRY", l + 435, mid_y - 180, colour=DC_LABEL_BULL, size=22,
           bg=(220, 255, 220))

    # VWAP reference line
    vwap_y = mid_y + 10
    draw.line([(l + 30, vwap_y), (r - 30, vwap_y)], fill=DC_VWAP, width=2)
    _label(draw, "VWAP", r - 80, vwap_y + 6, colour=DC_VWAP, size=22)

    # Entry / TP / SL
    entry_y = mid_y - 142
    sl_y    = mid_y - 100
    tp_y    = mid_y - 250
    _horizontal_dashed(draw, entry_y, l + 445, r - 100, colour=DC_BULL)
    _horizontal_dashed(draw, sl_y,    l + 445, r - 100, colour=DC_BEAR)
    _horizontal_dashed(draw, tp_y,    l + 445, r - 100, colour=DC_BULL)
    _label(draw, "ENTRY", r - 100, entry_y - 28, colour=DC_LABEL_BULL, size=22, anchor="right")
    _label(draw, "SL",    r - 100, sl_y + 4,     colour=DC_LABEL_BEAR, size=22, anchor="right")
    _label(draw, "TP",    r - 100, tp_y - 28,    colour=DC_LABEL_BULL, size=22, anchor="right")


# ── Diagram dispatch map ──────────────────────────────────────────────────────

def list_diagram_strategy_ids() -> list[str]:
    """Public list of strategy ids that have a visual diagram template."""
    return list(_DIAGRAM_MAP.keys())


_DIAGRAM_MAP: dict[str, Any] = {
    "order_block":         _diag_order_block,
    "fvg":                 _diag_fvg,
    "liquidity_grab":      _diag_liquidity_grab,
    "breaker_block":       _diag_breaker_block,
    "trend_pullback":      _diag_trend_pullback,
    "range_breakout":      _diag_range_breakout,
    "vwap_strategy":       _diag_vwap,
    "orb":                 _diag_orb,
    "sr_flip":             _diag_sr_flip,
    "trendline_break":     _diag_trendline_break,
    "consolidation_breakout": _diag_consolidation,
    "momentum_scalp":      _diag_momentum_scalp,
}

# Human-readable titles for the diagram post (short = powerful)
_DIAGRAM_TITLES: dict[str, str] = {
    "order_block":            "ORDER BLOCK ENTRY",
    "fvg":                    "FAIR VALUE GAP ENTRY",
    "liquidity_grab":         "LIQUIDITY HUNT",
    "breaker_block":          "BREAKER ENTRY",
    "trend_pullback":         "PULLBACK ENTRY",
    "range_breakout":         "RANGE BREAK ENTRY",
    "vwap_strategy":          "VWAP RECLAIM",
    "orb":                    "ORB BREAKOUT",
    "sr_flip":                "S/R FLIP TRADE",
    "trendline_break":        "TRENDLINE BREAK",
    "consolidation_breakout": "FLAG BREAK ENTRY",
    "momentum_scalp":         "MOMENTUM SCALP",
}

# Trader-language strip (max ~6 words each line)
_DIAGRAM_INFO: dict[str, dict[str, str]] = {
    "order_block": {
        "entry": "Wait for rejection → enter here",
        "sl": "SL below OB low (strict)",
        "target": "Target next liquidity (2–3R)",
    },
    "fvg": {
        "entry": "Enter 50% gap fill only",
        "sl": "SL below full gap",
        "target": "Target swing high / next pool",
    },
    "liquidity_grab": {
        "entry": "Enter on reversal close",
        "sl": "SL past the sweep wick",
        "target": "Target opposite liquidity",
    },
    "breaker_block": {
        "entry": "Short the rejection wick",
        "sl": "SL above breaker high",
        "target": "Target next structure",
    },
    "trend_pullback": {
        "entry": "Enter EMA rejection close",
        "sl": "SL under 50 EMA",
        "target": "Target prior swing high",
    },
    "range_breakout": {
        "entry": "Enter on strong close out",
        "sl": "SL if back inside range",
        "target": "Target measured move",
    },
    "vwap_strategy": {
        "entry": "Long VWAP reclaim close",
        "sl": "SL on VWAP lost again",
        "target": "Target prior impulse move",
    },
    "orb": {
        "entry": "Enter ORB break close",
        "sl": "SL mid-range / opposite side",
        "target": "Target 1–2× OR height",
    },
    "sr_flip": {
        "entry": "Fade the retest rejection",
        "sl": "SL above broken zone",
        "target": "Target next demand",
    },
    "trendline_break": {
        "entry": "Enter retest failure candle",
        "sl": "SL back inside line",
        "target": "Target swing extreme (2–3R)",
    },
    "consolidation_breakout": {
        "entry": "Enter flag break close",
        "sl": "SL under flag low",
        "target": "Target impulse height",
    },
    "momentum_scalp": {
        "entry": "Buy first pullback only",
        "sl": "Tight SL 10–15 pts",
        "target": "Quick TP 15–30 pts",
    },
}


def _draw_diagram_info_strip(
    draw: ImageDraw.Draw,
    strategy_id: str,
    strip_top: int,
    strip_bot: int,
) -> None:
    """
    Render the Entry / SL / Target strip below the diagram in three columns.
    """
    info = _DIAGRAM_INFO.get(strategy_id, {})
    if not info:
        return

    col_w  = INNER_W // 3
    f_lbl  = _font("bold", 24)
    f_val  = _font("regular", 23)
    labels = [("ENTRY", info.get("entry", ""), DC_BULL),
              ("STOP LOSS", info.get("sl", ""),   DC_BEAR),
              ("TARGET",   info.get("target", ""), C_TARGET_HI)]

    for i, (lbl, val, colour) in enumerate(labels):
        cx = MARGIN + i * col_w
        # Vertical separator
        if i > 0:
            draw.line([(cx - 8, strip_top + 6), (cx - 8, strip_bot - 6)], fill=C_DIVIDER, width=1)
        draw.text((cx, strip_top), lbl, font=f_lbl, fill=colour)
        try:
            _, _, _, lh = draw.textbbox((0, 0), lbl, font=f_lbl)
        except Exception:
            lh = 24
        # Wrap value text
        wrap_at = _wrap_chars(f_val, col_w - 10)
        lines   = textwrap.wrap(val, width=wrap_at)
        vy = strip_top + lh + 4
        for line in lines[:1]:
            short = _truncate_words(line, 10)
            draw.text((cx, vy), short, font=f_val, fill=C_BODY)
            try:
                _, _, _, vlh = draw.textbbox((0, 0), short, font=f_val)
            except Exception:
                vlh = 23
            vy += vlh + 3


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC — create_visual_strategy_diagram
# ═══════════════════════════════════════════════════════════════════════════════

def create_visual_strategy_diagram(strategy_name: str) -> str:
    """
    Generate a 1080×1080 educational diagram image for the given strategy.

    Args:
        strategy_name: one of the keys in _DIAGRAM_MAP
                       (e.g. "order_block", "fvg", "momentum_scalp")

    Returns:
        Absolute file path to the saved PNG.

    Raises:
        ValueError: if strategy_name is not recognised.
    """
    print(f"[DEBUG] Entered image generator: create_visual_strategy_diagram({strategy_name!r})")
    key = strategy_name.lower().replace(" ", "_")
    draw_fn = _DIAGRAM_MAP.get(key)
    if draw_fn is None:
        available = ", ".join(_DIAGRAM_MAP.keys())
        raise ValueError(
            f"Unknown strategy diagram '{strategy_name}'. "
            f"Available: {available}"
        )

    title = _DIAGRAM_TITLES.get(key, strategy_name.upper())
    img, draw = _new_canvas()

    # ── Viral content: OB/FVG use build_*content() for hook + warning ───────────
    if key == "order_block":
        _diag_content = build_ob_content()
    elif key == "fvg":
        _diag_content = build_fvg_content()
    else:
        _diag_content = {"hook": _pick_aggressive_hook(), "warning": _pick_urgency_line()}
    _hook = _diag_content.get("hook") or _pick_aggressive_hook()
    _urg = _diag_content.get("warning") or _pick_urgency_line()

    bar_bottom = _draw_title_bar(draw, title, subtitle=_hook, compact=True)

    foot_h = FOOTER_H_SUBTLE
    footer_top = H - foot_h
    info_h = 72
    info_top = footer_top - info_h - 6
    info_bot = footer_top - 6
    psych_band = 44 if (key == "fvg" and _diag_content.get("psychology")) else 0
    diagram_bottom = info_top - 8 - psych_band
    urgency_h = 46
    band_top = bar_bottom + urgency_h + 6
    max_diagram_h = diagram_bottom - band_top
    dh = min(int(H * 0.75), max(120, max_diagram_h))
    diagram_top = diagram_bottom - dh
    if diagram_top < band_top:
        diagram_top = band_top

    _apply_chart_texture_background(
        img, bar_bottom, diagram_top, mode="light", base_rgb=C_BG, with_grid=False
    )
    urg = _urg
    f_u = _font("bold", 24)
    try:
        _, _, uu, uh = draw.textbbox((0, 0), urg, font=f_u)
    except Exception:
        uu, uh = INNER_W, 28
    ux = (W - uu) // 2
    uy = bar_bottom + 10
    draw.rounded_rectangle(
        [ux - 12, uy - 4, ux + uu + 12, uy + uh + 8],
        radius=10,
        fill=(255, 248, 235),
        outline=(210, 70, 40),
        width=2,
    )
    draw.text((ux, uy), urg, font=f_u, fill=(175, 40, 25))
    l, t, r, b = MARGIN - 12, diagram_top, W - MARGIN + 12, diagram_bottom
    _apply_chart_texture_background(img, t, b, mode="light", base_rgb=(252, 253, 255), with_grid=True)

    _draw_brand_footer(img, draw, bar_height=foot_h)

    if psych_band:
        psych_top = info_top - psych_band - 4
        f_psych = _font("regular", 28)
        psych = _truncate_words(_diag_content["psychology"], 10)
        try:
            _, _, pw, ph = draw.textbbox((0, 0), psych, font=f_psych)
        except Exception:
            pw, ph = INNER_W, 30
        draw.rectangle([MARGIN - 10, psych_top, W - MARGIN + 10, info_top - 4], fill=(252, 252, 254))
        draw.text((MARGIN + (INNER_W - pw) // 2, psych_top + 8), psych, font=f_psych, fill=(120, 120, 120))

    draw.rectangle([MARGIN - 10, info_top - 4, W - MARGIN + 10, info_bot + 4], fill=(248, 250, 255))
    draw.line([(MARGIN - 10, info_top - 4), (W - MARGIN + 10, info_top - 4)], fill=C_DIVIDER, width=1)
    _draw_diagram_info_strip(draw, key, info_top, info_bot)

    box = (l, t, r, b, r - l, b - t)

    draw_fn(img, draw, box)

    draw.rectangle([l, t, r, b], outline=(15, 80, 180), width=3)

    log.info("Diagram rendered: %s", key)
    return _save(img, f"diagram_{key}")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — STRATEGY IMAGE GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_strategy_image(post: dict[str, Any]) -> str:
    """
    Generate a 1080×1080 strategy post image.

    Args:
        post: dict with keys 'title' and 'content' (from strategy_agent)

    Returns:
        Absolute path to the saved PNG file.
    """
    print(f"[DEBUG] Entered image generator: generate_strategy_image({post.get('title', '?')!r})")
    try:
        return _generate_strategy_image_body(post)
    except Exception as e:
        print(f"[ERROR] Image generation failed: {e}")
        raise


def _strategy_key_bullets(sections: dict[str, Any]) -> list[str]:
    """Up to 3 lines, max 6 words each — numbered emoji in renderer."""
    out: list[str] = []
    concept = sections.get("concept", "")
    if concept:
        if isinstance(concept, str):
            out.append(_truncate_words(concept, 6))
        else:
            out.append(_truncate_words(str(concept), 6))
    entry = sections.get("entry", [])
    if isinstance(entry, list):
        for e in entry[:2]:
            out.append(_truncate_words(_clean_bullet(str(e)), 6))
    elif entry:
        out.append(_truncate_words(str(entry), 6))
    return out[:3]


def _generate_strategy_image_body(post: dict[str, Any]) -> str:
    title   = _strip_emoji_markers(post.get("title", "STRATEGY"))
    title   = re.sub(r"^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF]+\s*", "", title)
    content = post.get("content", "")
    tags    = post.get("tags", [])

    sections = parse_strategy_content(content)

    img, draw = _new_canvas()

    y = _draw_viral_title_bar(draw, title, subtitle="SMC Strategy")
    foot_h = FOOTER_H_SUBTLE
    strip_top = H - foot_h - CHART_STRIP_H
    _apply_chart_texture_background(img, y, strip_top, mode="light", base_rgb=C_BG, with_grid=False)
    _apply_chart_texture_background(
        img, strip_top, H - foot_h, mode="light", base_rgb=(248, 250, 255), with_grid=True
    )
    y += 22

    bullets = _strategy_key_bullets(sections)
    f_b = _font("bold", 26)
    f_body = _font("regular", 26)
    for i, line in enumerate(bullets[:3]):
        card_top = y
        bln = (_wrap_max_words_per_line(line, 6) or [line])[0]
        prefix = f"{_EMOJI_NUM[i]} "
        full = prefix + bln
        try:
            _, _, _, bh = draw.textbbox((0, 0), full, font=f_body)
        except Exception:
            bh = 30
        card_h = bh + 28
        _draw_rounded_shadow_card(
            draw,
            (MARGIN - 10, card_top, W - MARGIN + 10, card_top + card_h),
            fill=C_TAG_BG,
            radius=14,
            shadow_rgb=(185, 195, 210),
        )
        draw.text((MARGIN + 14, card_top + 12), prefix, font=f_b, fill=C_ACCENT)
        try:
            _, _, pw, _ = draw.textbbox((0, 0), prefix, font=f_b)
        except Exception:
            pw = 52
        draw.text((MARGIN + 14 + pw, card_top + 12), bln, font=f_body, fill=C_BODY)
        y += card_h + 12

    sl = sections.get("stop_loss", "")
    tgt = sections.get("target", "")
    best = sections.get("best_market", "")
    row_y = y + 10
    f_lbl = _font("bold", 22)
    f_val = _font("regular", 22)
    col_w = INNER_W // 3
    if sl or tgt or best:
        x0 = MARGIN
        draw.text((x0, row_y), "ENTRY", font=f_lbl, fill=C_ENTRY_HI)
        draw.text((x0, row_y + 28), "Use rules above", font=f_val, fill=C_BODY)
        x1 = MARGIN + col_w
        draw.text((x1, row_y), "SL", font=f_lbl, fill=C_SL_HI)
        sl_t = _truncate_words(str(sl), 10) if sl else "—"
        draw.text((x1, row_y + 28), sl_t, font=f_val, fill=C_BODY)
        x2 = MARGIN + 2 * col_w
        draw.text((x2, row_y), "TARGET", font=f_lbl, fill=C_TARGET_HI)
        tg_t = _truncate_words(str(tgt), 10) if tgt else "—"
        draw.text((x2, row_y + 28), tg_t, font=f_val, fill=C_BODY)
        y = row_y + 72

    if best:
        f_m = _font("bold", 22)
        draw.text((MARGIN, y), "Works best:", font=f_m, fill=C_ACCENT)
        try:
            _, _, _, mh = draw.textbbox((0, 0), "Works best:", font=f_m)
        except Exception:
            mh = 22
        draw.text(
            (MARGIN + 140, y),
            _truncate_words(str(best), 10),
            font=f_body,
            fill=C_BODY,
        )
        y += max(mh, 28) + 8

    if tags and y < strip_top - 50:
        f_tag = _font("bold", 20)
        tag_x = MARGIN
        for tag in tags[:3]:
            try:
                _, _, tw, th = draw.textbbox((0, 0), tag, font=f_tag)
            except Exception:
                tw, th = 60, 20
            pad = 10
            draw.rounded_rectangle(
                [tag_x - pad, y - 4, tag_x + tw + pad, y + th + 4],
                radius=10, fill=C_TAG_BG,
            )
            draw.text((tag_x, y), tag, font=f_tag, fill=C_ACCENT)
            tag_x += tw + pad * 2 + 8

    risk_raw = sections.get("risk", "")
    if risk_raw:
        risk_str = str(risk_raw).lower()
        risk_level = "high" if "high" in risk_str else ("medium" if "medium" in risk_str else "low")
        risk_colour = C_CONFIDENCE.get(risk_level, C_BODY)
        risk_label = _risk_label_trader_tone(risk_raw)
        f_risk = _font("bold", 22)
        try:
            _, _, rw, rh = draw.textbbox((0, 0), risk_label, font=f_risk)
        except Exception:
            rw, rh = 200, 24
        ry = min(y + 10, strip_top - 40)
        draw.rounded_rectangle([MARGIN - 4, ry - 4, MARGIN + rw + 12, ry + rh + 4], radius=8, fill=risk_colour)
        draw.text((MARGIN, ry), risk_label, font=f_risk, fill=(255, 255, 255))

    f_cta = _font("bold", 22)
    cta = _pick_micro_cta()
    try:
        _, _, cw, ch = draw.textbbox((0, 0), cta, font=f_cta)
    except Exception:
        cw, ch = INNER_W, 24
    cta_y = min(strip_top - 18, H - foot_h - CHART_STRIP_H - 30)
    draw.text(((W - cw) // 2, cta_y), cta, font=f_cta, fill=C_ACCENT)

    _draw_brand_footer(img, draw, bar_height=FOOTER_H_SUBTLE)

    return _save(img, "strategy")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 7 — NEWS IMAGE GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

_IMPACT_COLOURS = {
    "bullish": (22, 135, 60),    # green
    "bearish": (190, 40, 40),    # red
    "neutral": (80, 80, 180),    # purple-blue
}

_CONFIDENCE_ICONS = {"high": "●●●", "medium": "●●○", "low": "●○○"}


def _news_series_badge_text(item: dict[str, Any]) -> str | None:
    """Return label like 'PART 1 OF 2' when this post is one of a multi-image series."""
    part = item.get("part")
    total = item.get("total_parts")
    if part is None or total is None:
        return None
    try:
        p, t = int(part), int(total)
    except (TypeError, ValueError):
        return None
    if t < 2 or p < 1 or p > t:
        return None
    return f"PART {p} OF {t}"


def _draw_series_badge_on_bar(draw: ImageDraw.Draw, text: str) -> None:
    """Top-right badge on the blue MARKET NEWS title bar — dark pill + white text (no empty-looking block)."""
    f = _font("bold", 21)
    try:
        _, _, tw, th = draw.textbbox((0, 0), text, font=f)
    except Exception:
        tw, th = len(text) * 11, 24
    px = W - MARGIN - tw - 18
    py = 20
    # Darker than title bar so it reads as a real badge, not a washed-out white patch
    draw.rounded_rectangle(
        [px - 12, py - 5, px + tw + 12, py + th + 5],
        radius=10,
        fill=(8, 45, 110),
        outline=(255, 255, 255),
        width=1,
    )
    draw.text((px, py), text, font=f, fill=(255, 255, 255))


def _draw_series_badge_themed(draw: ImageDraw.Draw, theme: dict, text: str, y: float = 48.0) -> None:
    """Badge for themed headline cards (dark/light)."""
    f = _font("bold", 22)
    try:
        _, _, tw, th = draw.textbbox((0, 0), text, font=f)
    except Exception:
        tw, th = len(text) * 11, 24
    px = W - MARGIN - tw - 20
    py = int(y)
    draw.rounded_rectangle(
        [px - 10, py - 5, px + tw + 10, py + th + 6],
        radius=10,
        fill=theme["accent"],
    )
    draw.text((px, py), text, font=f, fill=(255, 255, 255))


def _guess_sector_watchlist(stock: str) -> list[str]:
    """Plausible NSE tickers / labels for caption context (not trading advice)."""
    u = stock.upper().replace(" ", "")
    if "METAL" in u or "STEEL" in u or "BASE METAL" in stock.upper():
        return ["HINDALCO", "TATASTEEL", "JSWSTEEL", "SAIL", "NALCO", "NMDC"]
    if "IT" in u or "TECH" in u or "SOFTWARE" in u:
        return ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM"]
    if "BANK" in u or "HDFC" in u or "ICICI" in u or "KOTAK" in u or "AXIS" in u or "SBIN" in u:
        return ["HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN"]
    if "PHARMA" in u or "DRUG" in u:
        return ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "LUPIN"]
    if "AUTO" in u or "OEM" in u:
        return ["MARUTI", "M&M", "TATAMOTORS", "EICHER", "BAJAJ-AUTO"]
    if "ENERGY" in u or "OIL" in u or "CRUDE" in u:
        return ["RELIANCE", "ONGC", "COALINDIA", "IOC", "BPCL"]
    if "NIFTY" in u and "IT" in u:
        return ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"]
    if "FII" in u or "FLOW" in u:
        return ["NIFTY50", "BANKNIFTY", "Index heavyweights", "Volatile large-caps"]
    if "INFLATION" in u or "CPI" in u or "RBI" in u:
        return ["Rate-sensitive banks", "NBFC leaders", "Realty index names"]
    return ["Large-cap index names", "Sector ETFs", "Top-5 volume leaders"]


def _trade_to_short_bullets(trade: str, max_bullets: int = 3, max_words: int = 10) -> list[str]:
    """Split trade copy into short scan lines (no paragraphs)."""
    t = (trade or "").strip()
    if not t:
        return []
    t = re.sub(r"(?i)execution:\s*", "; ", t)
    parts = re.split(r"[.;]+", t)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        line = _truncate_words(p, max_words)
        if line and line not in out:
            out.append(line)
        if len(out) >= max_bullets:
            return out[:max_bullets]
    if not out:
        out = _wrap_max_words_per_line(_truncate_words(t, 30), max_words)[:max_bullets]
    return out[:max_bullets]


def _enrich_news_item(item: dict[str, Any]) -> dict[str, Any]:
    """
    Expand agent strings — viral layout uses **short** lines; long fields kept for compatibility.
    Explicit keys on ``item`` override: event_expanded, impact_expanded, trade_expanded, watchlist, risk_note.
    """
    stock = item.get("stock", "MARKET")
    event = (item.get("event") or "").strip()
    impact = (item.get("impact") or "").strip()
    trade = (item.get("trade_idea") or "").strip()

    event_exp = (item.get("event_expanded") or "").strip()
    impact_exp = (item.get("impact_expanded") or "").strip()
    trade_exp = (item.get("trade_expanded") or "").strip()

    if not event_exp:
        event_exp = (
            f"{event} "
            "Liquid names often move first—watch volume before sizing up."
        ).strip()

    if not impact_exp:
        impact_exp = (
            f"{impact} "
            "Leaders can trend faster than smaller names—use sensible stops."
        ).strip()

    if not trade_exp:
        trade_exp = (
            f"{trade}. "
            "Add on retests; trail only after a 5m or 15m chart confirms."
        ).strip()

    # Scan-friendly single lines (carousel + intel card)
    event_line = _truncate_words(event or event_exp, 12)
    impact_line = _truncate_words(impact or impact_exp, 12)
    trade_line = _truncate_words(trade or trade_exp, 12)
    trade_bullets = _trade_to_short_bullets(trade_exp, max_bullets=3, max_words=10)

    watch: list[str] = item.get("watchlist") if isinstance(item.get("watchlist"), list) else []
    if not watch:
        watch = _guess_sector_watchlist(stock)

    risk = (item.get("risk_note") or "").strip()
    if not risk:
        risk = (
            "Gaps and headlines can move prices fast. Size to your daily cap. "
            "Educational only—verify yourself."
        )

    return {
        "event_long": event_exp,
        "impact_long": impact_exp,
        "trade_long": trade_exp,
        "event_line": event_line,
        "impact_line": impact_line,
        "trade_line": trade_line,
        "trade_bullets": trade_bullets,
        "watchlist": watch[:8],
        "risk_line": risk,
    }


def _news_flash_line(event: str, stock: str) -> str:
    """One aggressive headline line + optional sector emoji."""
    ev = (event or "").strip()
    if not ev:
        return stock.upper()
    core = _extract_headline(ev, stock).upper()
    if len(core) < 4:
        core = _truncate_words(ev.upper(), 10)
    emoji = ""
    u = ev.upper()
    if any(x in u for x in ("CRUDE", "OIL", "WTI", "BRENT", "PETROL")):
        emoji = " ⛽"
    elif "BANK" in u or "RBI" in u:
        emoji = " 🏦"
    elif "METAL" in u or "STEEL" in u:
        emoji = " ⚙️"
    elif "IT" in u or "TECH" in u:
        emoji = " 💻"
    return core + emoji


def _news_impact_arrow(impact: str, direction: str) -> str:
    imp = _truncate_words((impact or "MARKETS IN FOCUS").strip(), 8).upper()
    if direction == "BULLISH":
        return f"→ {imp} — BULLISH"
    if direction == "BEARISH":
        return f"→ {imp} — BEARISH"
    return f"→ {imp}"


def generate_news_image(news_item: dict[str, Any]) -> str:
    """
    Generate a 1080×1080 market intelligence post image.

    Args:
        news_item: dict with keys ``stock``, ``event``, ``impact``, ``trade_idea``,
            ``confidence``, ``source`` (from news_agent). Optional:

            - ``part`` / ``total_parts``: show **PART N OF M** badge (e.g. Part 1 of 2
              for the intel card paired with :func:`generate_headline_post` as Part 2).
            - ``event_expanded``, ``impact_expanded``, ``trade_expanded``: override
              auto-expanded copy.
            - ``watchlist``: list of ticker strings for **SECTOR / NAMES IN FOCUS**.
            - ``risk_note``: custom risk / disclaimer line.

    Returns:
        Absolute path to the saved PNG file.
    """
    print(f"[DEBUG] Entered image generator: generate_news_image(stock={news_item.get('stock', '?')!r})")
    try:
        return _generate_news_image_body(news_item)
    except Exception as e:
        print(f"[ERROR] Image generation failed: {e}")
        raise


def _generate_news_image_body(news_item: dict[str, Any]) -> str:
    """
    Viral news layout: build_news_content → hook, title, psychology, EVENT/IMPACT, trade box.
    """
    content = build_news_content(news_item)
    img, draw = _new_canvas()

    bar_y = _draw_title_bar(draw, content["title"], subtitle=content["hook"])
    badge = _news_series_badge_text(news_item)
    if badge:
        _draw_series_badge_on_bar(draw, badge)

    foot_h = FOOTER_H_SUBTLE
    strip_top = H - foot_h - CHART_STRIP_H
    _apply_chart_texture_background(
        img, bar_y, strip_top, mode="light", base_rgb=C_BG, with_grid=False
    )
    _apply_chart_texture_background(
        img, strip_top, H - foot_h, mode="light", base_rgb=(248, 250, 255), with_grid=True
    )

    y = bar_y + 28
    f_psych = _font("regular", 28)
    for ln in _wrap_max_words_per_line(content["psychology"], 10)[:1]:
        try:
            _, _, pw, ph = draw.textbbox((0, 0), ln, font=f_psych)
        except Exception:
            pw, ph = INNER_W, 30
        draw.text(((W - pw) // 2, y), ln, font=f_psych, fill=(120, 120, 120))
        y += ph + 20
    y += 8

    f_lab = _font("bold", 22)
    draw.text((MARGIN, y), "EVENT", font=f_lab, fill=C_ACCENT)
    try:
        _, _, _, lh = draw.textbbox((0, 0), "EVENT", font=f_lab)
    except Exception:
        lh = 24
    y += lh + 6
    f_ev = _font("regular", 30)
    for ln in _wrap_max_words_per_line(content["event"], 12)[:2]:
        try:
            _, _, ew, eh = draw.textbbox((0, 0), ln, font=f_ev)
        except Exception:
            ew, eh = INNER_W, 32
        draw.text((MARGIN, y), ln, font=f_ev, fill=C_BODY)
        y += eh + 4
    y += 16

    draw.text((MARGIN, y), "IMPACT", font=f_lab, fill=C_ACCENT)
    y += lh + 6
    for ln in _wrap_max_words_per_line(content["impact"], 12)[:2]:
        try:
            _, _, iw, ih = draw.textbbox((0, 0), ln, font=f_ev)
        except Exception:
            iw, ih = INNER_W, 32
        draw.text((MARGIN, y), ln, font=f_ev, fill=C_BODY)
        y += ih + 4
    y += 24

    trade_txt = f"👉 {_truncate_words(content['trade'], 8)}"
    f_trade = _font("bold", 34)
    _draw_rounded_shadow_card(
        draw,
        (MARGIN - 8, y - 6, W - MARGIN + 8, y + 56),
        fill=C_TAG_BG,
        radius=16,
        shadow_rgb=(190, 198, 212),
    )
    draw.rectangle([MARGIN - 8, y - 6, MARGIN + 6, y + 56], fill=C_ACCENT)
    draw.text((MARGIN + 18, y + 8), trade_txt, font=f_trade, fill=C_BODY)
    y += 72

    f_wait = _font("bold", 24)
    wait_txt = "Wait for confirmation."
    try:
        _, _, ww, wh = draw.textbbox((0, 0), wait_txt, font=f_wait)
    except Exception:
        ww, wh = INNER_W, 26
    if y + wh < strip_top - 10:
        draw.text(((W - ww) // 2, y), wait_txt, font=f_wait, fill=(190, 55, 40))

    _draw_brand_footer(img, draw, bar_height=FOOTER_H_SUBTLE)

    part = news_item.get("part")
    tot = news_item.get("total_parts")
    if part is not None and tot is not None:
        try:
            return _save(img, f"news_p{int(part)}of{int(tot)}")
        except (TypeError, ValueError):
            pass
    return _save(img, "news")


# ═══════════════════════════════════════════════════════════════════════════════
# THEME ENGINE
# Provides light + dark design tokens so every new post type can switch
# ═══════════════════════════════════════════════════════════════════════════════

import random as _random

# Theme tokens — (bg, fg, accent, muted, card_bg, card_border)
_THEMES = {
    "light": {
        "bg":           (255, 255, 255),
        "fg":           (15,  15,  15),
        "accent":       (15,  80, 180),
        "accent2":      (230, 100,  20),   # orange highlights
        "muted":        (120, 120, 120),
        "card_bg":      (242, 246, 255),
        "card_border":  (200, 215, 245),
        "green":        (22,  140,  60),
        "red":          (200,  35,  35),
        "check":        (22,  140,  60),
        "bar_bg":       (15,  80, 180),
        "bar_fg":       (255, 255, 255),
        "footer_bg":    (15,  80, 180),
        "footer_fg":    (255, 255, 255),
    },
    "dark": {
        "bg":           (12,  14,  22),
        "fg":           (235, 235, 245),
        "accent":       (82, 140, 255),
        "accent2":      (255, 160,  40),
        "muted":        (150, 155, 170),
        "card_bg":      (22,  28,  46),
        "card_border":  (50,  65, 110),
        "green":        (50, 200, 100),
        "red":          (255,  75,  75),
        "check":        (50, 200, 100),
        "bar_bg":       (22,  28,  46),
        "bar_fg":       (82, 140, 255),
        "footer_bg":    (8,   10,  18),
        "footer_fg":    (160, 175, 220),
    },
}


def _pick_theme(force: str | None = None) -> dict:
    """Return a random theme dict, or a forced one."""
    key = force if force in _THEMES else _random.choice(list(_THEMES.keys()))
    return {"name": key, **_THEMES[key]}


def _theme_canvas(theme: dict) -> tuple[Image.Image, ImageDraw.Draw]:
    img  = Image.new("RGB", (W, H), theme["bg"])
    draw = ImageDraw.Draw(img)
    return img, draw


# ── Shared themed header / footer ─────────────────────────────────────────────

def _themed_header(draw: ImageDraw.Draw, theme: dict, title: str, subtitle: str = "") -> int:
    """Blue (light) or dark-card (dark) header bar. Returns y below bar."""
    bar_h = 200 if subtitle else 165
    draw.rectangle([(0, 0), (W, bar_h)], fill=theme["bar_bg"])

    if subtitle:
        f_sub = _font("bold", 26)
        sub_upper = subtitle.upper()
        try:
            _, _, sw, sh = draw.textbbox((0, 0), sub_upper, font=f_sub)
        except Exception:
            sw, sh = 200, 26
        # Pill for subtitle
        px = (W - sw) // 2
        py = 22
        draw.rounded_rectangle([px - 18, py - 6, px + sw + 18, py + sh + 6],
                                radius=14, fill=theme["accent"])
        draw.text((px, py), sub_upper, font=f_sub, fill=theme["bar_fg"])
        title_y = py + sh + 18
    else:
        title_y = 34

    for size in (72, 60, 50, 42, 36):
        f = _font("bold", size)
        try:
            _, _, tw, th = draw.textbbox((0, 0), title.upper(), font=f)
        except Exception:
            tw, th = W, size
        if tw <= INNER_W:
            x = (W - tw) // 2
            draw.text((x, title_y), title.upper(), font=f, fill=theme["bar_fg"])
            title_y += th
            break

    return bar_h + 10


_LOGO_PATH_IG = Path(__file__).parent.parent / "assets" / "logo.png"
_LOGO_IMG_IG: "Image.Image | None" = None


def _get_logo_ig() -> "Image.Image | None":
    global _LOGO_IMG_IG
    if _LOGO_IMG_IG is not None:
        return _LOGO_IMG_IG
    if _LOGO_PATH_IG.exists():
        try:
            _LOGO_IMG_IG = Image.open(_LOGO_PATH_IG).convert("RGBA")
            return _LOGO_IMG_IG
        except Exception:
            pass
    return None


def _paste_logo_watermark(img: Image.Image, opacity: float = 0.07) -> None:
    """Paste the logo as a faint centred watermark on the canvas (in-place)."""
    logo = _get_logo_ig()
    if logo is None:
        return
    logo_w = int(W * 0.32)
    logo_h = int(logo.height * logo_w / logo.width)
    logo_r = logo.resize((logo_w, logo_h), Image.LANCZOS)
    r, g, b, a = logo_r.split()
    a = a.point(lambda x: int(x * opacity))
    logo_faint = Image.merge("RGBA", (r, g, b, a))
    px = (W - logo_w) // 2
    py = (H - logo_h) // 2
    img.paste(logo_faint, (px, py), logo_faint)


def _paste_logo_footer(img: Image.Image, bar_top: int, bar_height: int = 72) -> None:
    """Paste a small logo icon in the footer bar (left side)."""
    logo = _get_logo_ig()
    if logo is None:
        return
    logo_h = bar_height - 16
    logo_w = int(logo.width * logo_h / logo.height)
    logo_sm = logo.resize((logo_w, logo_h), Image.LANCZOS)
    r, g, b, a = logo_sm.split()
    a = a.point(lambda x: int(x * 0.80))
    logo_sm = Image.merge("RGBA", (r, g, b, a))
    img.paste(logo_sm, (MARGIN, bar_top + 8), logo_sm)


def _themed_footer(draw: ImageDraw.Draw, theme: dict, brand: str = "StocksWithGaurav",
                   img: "Image.Image | None" = None,
                   bar_height: int = FOOTER_H_SUBTLE) -> None:
    bar_height = max(44, min(bar_height, 96))
    bar_top = H - bar_height
    draw.rectangle([(0, bar_top), (W, H)], fill=theme["footer_bg"])
    fs = 24 if bar_height <= FOOTER_H_SUBTLE else 32
    f = _font("bold", fs)
    try:
        _, _, tw, th = draw.textbbox((0, 0), brand, font=f)
    except Exception:
        tw, th = 220, fs
    ty = bar_top + (bar_height - th) // 2
    draw.text(((W - tw) // 2, ty), brand, font=f, fill=theme["footer_fg"])
    base = img if img is not None else getattr(draw, "im", None) or getattr(draw, "_image", None)
    if base is not None:
        _paste_logo_footer(base, bar_top, bar_height)


# ── Divider line in theme colour ──────────────────────────────────────────────

def _themed_divider(draw: ImageDraw.Draw, theme: dict, y: int,
                    x0: int = MARGIN, x1: int = W - MARGIN) -> int:
    colour = theme["card_border"] if theme["name"] == "dark" else C_DIVIDER
    draw.line([(x0, y), (x1, y)], fill=colour, width=1)
    return y + 1


# ═══════════════════════════════════════════════════════════════════════════════
# CHEAT SHEET STRATEGY DATABASE
# Covers all 12 strategies — compact format for generate_cheatsheet_post
# ═══════════════════════════════════════════════════════════════════════════════

_CHEATSHEET_DB: dict[str, dict[str, Any]] = {
    "order_block": {
        "title": "ORDER BLOCK",
        "concept": "Institutions leave imbalanced candles; price returns to retest before resuming.",
        "entry":   ["Rejection candle at OB zone", "50–100% retracement into OB", "Confirm with FVG or HTF structure"],
        "sl":      "Below OB low (5–10 pts NIFTY)",
        "target":  "2R–3R | Next liquidity pool",
        "avoid":   ["Entering before price reaches OB", "Ignoring HTF trend direction", "OB already violated — skip it"],
    },
    "fvg": {
        "title": "FAIR VALUE GAP",
        "concept": "3-candle imbalance where gap between candle 1 & 3 is magnetic for price.",
        "entry":   ["Wait for price to enter FVG zone", "Enter near 50% of the gap", "Volume on impulse validates it"],
        "sl":      "Full FVG invalidated on close through",
        "target":  "1.5R–3R | Previous swing high/low",
        "avoid":   ["Trading FVG against HTF trend", "Entering before price reaches gap", "Chasing if gap already filled"],
    },
    "liquidity_grab": {
        "title": "LIQUIDITY GRAB",
        "concept": "Smart money sweeps stop clusters (equal highs/lows) before reversing sharply.",
        "entry":   ["Wick pierces beyond equal highs/lows", "Candle CLOSES back inside the range", "Enter on close or next candle open"],
        "sl":      "Beyond the hunt wick extreme",
        "target":  "2R minimum | Opposite pool",
        "avoid":   ["Entering before the candle closes", "Fading every wick (requires equal level)", "Ignoring macro trend bias"],
    },
    "breaker_block": {
        "title": "BREAKER BLOCK",
        "concept": "A broken OB flips polarity — old demand becomes supply (and vice versa).",
        "entry":   ["OB violated with BOS/CHoCH", "Price pulls back to former OB", "Rejection candle confirms the flip"],
        "sl":      "Above the Breaker Block zone",
        "target":  "3R | Next key liquidity target",
        "avoid":   ["Using without prior BOS confirmation", "Entering without rejection candle", "Breaker too far from current price"],
    },
    "trend_pullback": {
        "title": "TREND PULLBACK",
        "concept": "In a strong trend price pulls back to EMA before resuming — ride the continuation.",
        "entry":   ["Confirm uptrend: price above 20 & 50 EMA", "Wait for 3–7 candle pullback to 20 EMA", "Rejection candle (engulf/pin bar) at EMA"],
        "sl":      "Below 50 EMA | 5-min close under 20 EMA",
        "target":  "2R minimum | Previous swing high",
        "avoid":   ["Trading pullback against strong momentum", "Entering during news events", "Ignoring RSI — avoid extreme readings"],
    },
    "range_breakout": {
        "title": "RANGE BREAKOUT",
        "concept": "Price coils in a range; volume-backed breakout signals directional commitment.",
        "entry":   ["15-min candle CLOSE above resistance", "Volume above 20-period average", "Range must be at least 40 pts wide (NIFTY)"],
        "sl":      "Back inside the range (10–15 pts below breakout)",
        "target":  "Range height projected from breakout",
        "avoid":   ["Chasing — enter on close, not the wick", "Range less than 1 hour old", "Low-volume breakout = fakeout risk"],
    },
    "vwap_strategy": {
        "title": "VWAP STRATEGY",
        "concept": "VWAP = institutional fair value. Reclaim = bullish; fail = bearish.",
        "entry":   ["Bullish: close back above VWAP", "Best in first 90 min of session", "Confluence with OB or prior POC"],
        "sl":      "Re-cross of VWAP on 5-min close",
        "target":  "1.5× the initial move off VWAP",
        "avoid":   ["Trading VWAP in low-volume sessions", "Re-entering after 3rd VWAP cross", "Using on weekly — intraday only"],
    },
    "orb": {
        "title": "OPENING RANGE BREAKOUT",
        "concept": "First 15-min candle sets battleground. Breakout defines the day's bias.",
        "entry":   ["Mark 09:15–09:30 candle high/low", "Enter on 15-min close beyond range", "Align with pre-market gap direction"],
        "sl":      "Mid-point of ORB range",
        "target":  "1×–2× ORB height from breakout",
        "avoid":   ["ORB range < 40 pts = too narrow, skip", "Entering on wick — wait for close", "Counter-gap direction trades"],
    },
    "sr_flip": {
        "title": "S/R FLIP",
        "concept": "Broken support becomes new resistance; price revisits to offer a rejection entry.",
        "entry":   ["Support broken with momentum (no wick)", "Pull-back to former support (now resistance)", "Rejection candle confirms the flip"],
        "sl":      "Above the flipped zone (zone high + buffer)",
        "target":  "Next support level | Measured breakdown",
        "avoid":   ["Flipping a single-touch S/R level", "Trading the flip immediately after break", "Ignoring volume on the breakdown"],
    },
    "trendline_break": {
        "title": "TRENDLINE BREAK",
        "concept": "A trendline with 3+ touches is valid; a body-close break signals exhaustion.",
        "entry":   ["3+ swing touches on trendline", "Candle BODY closes beyond trendline", "Enter on retest from the broken side"],
        "sl":      "Back inside trendline by > 10 pts",
        "target":  "2R–3R | Opposite swing extreme",
        "avoid":   ["Only 2 touches — not a valid trendline", "Entering on the break (wait for retest)", "Using on low-timeframe noise < 15 min"],
    },
    "consolidation_breakout": {
        "title": "FLAG BREAKOUT",
        "concept": "Tight flag after a strong impulse = energy coiling. Breakout resumes impulse.",
        "entry":   ["Impulse followed by 5–15 candle drift", "Enter on close above flag resistance", "Volume contracts during flag, expands on break"],
        "sl":      "Below flag consolidation low",
        "target":  "Impulse height added to breakout point",
        "avoid":   ["Flag lasting more than 20 candles (base, not flag)", "Counter-impulse direction trades", "Entry candle wider than 15 pts — skip"],
    },
    "momentum_scalp": {
        "title": "MOMENTUM SCALP",
        "concept": "Capture 20–40 pt bursts on NIFTY. Quality over quantity — max 2 scalps/session.",
        "entry":   ["3 consecutive strong candles one direction", "Price above VWAP for longs (below for shorts)", "Enter on first 1–2 candle pullback"],
        "sl":      "10–15 pts NIFTY | 25–35 pts BANKNIFTY",
        "target":  "15–30 pts NIFTY | 40–60 pts BANKNIFTY",
        "avoid":   ["More than 2 scalps per session", "Scalping against VWAP direction", "No momentum = no trade (wait for burst)"],
    },
}

# ── Tips database — cycled daily ──────────────────────────────────────────────

_TIPS_DB: list[dict[str, Any]] = [
    {
        "title": "3 RULES FOR PROFITABLE TRADING",
        "subtitle": "Master these before entering any trade",
        "tips": ["Trade with the trend — not against it",
                 "Always define your SL before entry",
                 "1% risk per trade, every single time"],
        "colour": "green",
    },
    {
        "title": "SMC ENTRY CHECKLIST",
        "subtitle": "Tick all boxes — or wait",
        "tips": ["HTF structure confirmed (bullish/bearish)",
                 "Key zone identified (OB / FVG / Liquidity)",
                 "Confluence on lower timeframe entry",
                 "SL placed at zone invalidation level"],
        "colour": "blue",
    },
    {
        "title": "WHY TRADERS LOSE MONEY",
        "subtitle": "Avoid these 3 costly mistakes",
        "tips": ["FOMO entries — chasing extended moves",
                 "Moving SL to avoid loss (SL exists for a reason)",
                 "Overtrading — more trades = more losses"],
        "colour": "red",
    },
    {
        "title": "MORNING SESSION RULES",
        "subtitle": "First 45 minutes — the golden window",
        "tips": ["Mark the ORB (09:15–09:30 range)",
                 "Wait for a confirmed direction break",
                 "VWAP reclaim / rejection is your bias filter",
                 "News events before open = wider SL or skip"],
        "colour": "orange",
    },
    {
        "title": "RISK MANAGEMENT BIBLE",
        "subtitle": "Non-negotiable rules",
        "tips": ["Never risk > 1% of capital per trade",
                 "Max 3 losing trades = stop for the day",
                 "Target 2R minimum on every setup",
                 "Journal every trade — no exceptions"],
        "colour": "blue",
    },
    {
        "title": "WHEN NOT TO TRADE",
        "subtitle": "Patience is the edge",
        "tips": ["Major economic events (RBI, Budget, US CPI)",
                 "First 5 minutes of market open",
                 "When daily loss limit is hit",
                 "When you feel emotional or frustrated"],
        "colour": "red",
    },
    {
        "title": "5 SMC CONCEPTS TO MASTER",
        "subtitle": "Core of Smart Money trading",
        "tips": ["BOS — Break of Structure (trend shift)",
                 "CHoCH — Change of Character (reversal signal)",
                 "OB — Order Block (institutional entry zone)",
                 "FVG — Fair Value Gap (imbalance magnet)",
                 "Liquidity — Equal highs/lows = stop clusters"],
        "colour": "green",
    },
]

# Tips colour → accent override (used when dark/light theme chosen)
_TIPS_ACCENT = {
    "green":  (22,  140,  60),
    "red":    (200,  35,  35),
    "blue":   (15,   80, 180),
    "orange": (210, 120,  20),
}


def quick_tips_theme_count() -> int:
    """Number of distinct quick-tips themes in :data:`_TIPS_DB` (for preview / sampling)."""
    return len(_TIPS_DB)


# ═══════════════════════════════════════════════════════════════════════════════
# POST TYPE 2 — CHEATSHEET
# ═══════════════════════════════════════════════════════════════════════════════

def generate_cheatsheet_post(
    strategy_id: str | None = None,
    theme_name: str | None = None,
) -> str:
    """
    Generate a cheat-sheet card (1–4 parts) for a given strategy.

    Large strategies are automatically split across Part 1, Part 2, etc.
    Each part is saved to disk; this function returns the path to Part 1.

    Args:
        strategy_id: key from _CHEATSHEET_DB (random if None)
        theme_name:  "light" | "dark" | None (random)

    Returns:
        Absolute path to Part 1 PNG.
    """
    print(f"[DEBUG] Entered image generator: generate_cheatsheet_post({strategy_id!r})")
    try:
        return _generate_cheatsheet_post_body(strategy_id, theme_name)
    except Exception as e:
        print(f"[ERROR] Image generation failed: {e}")
        raise


def _cheatsheet_section_height(draw, f_lbl, f_body, label, content, theme) -> int:
    """Estimate pixel height of a cheatsheet section."""
    try:
        _, _, _, lh = draw.textbbox((0, 0), label, font=f_lbl)
    except Exception:
        lh = 28
    total = lh + 8 + 14 + 1 + 14   # label + gap + divider+padding

    if isinstance(content, list):
        for item in content:
            wrap_at = _wrap_chars(f_body, INNER_W - 30)
            lines = textwrap.wrap(item, width=wrap_at) or [item]
            for ln in lines:
                try:
                    _, _, _, vlh = draw.textbbox((0, 0), ln, font=f_body)
                except Exception:
                    vlh = 26
                total += vlh + 4
            total += 2
    else:
        wrap_at = _wrap_chars(f_body, INNER_W)
        for ln in textwrap.wrap(str(content), width=wrap_at) or [""]:
            try:
                _, _, _, lh2 = draw.textbbox((0, 0), ln, font=f_body)
            except Exception:
                lh2 = 26
            total += lh2 + 4
    return total


def _draw_chart_reference(draw: ImageDraw.Draw, img: Image.Image,
                           strategy_id: str, theme: dict,
                           x0: int, y0: int, x1: int, y1: int) -> None:
    """
    Draw a compact chart reference diagram inside the bounding box [x0,y0,x1,y1].
    Uses the same PIL drawing primitives as create_visual_strategy_diagram but
    sized to fit inline within a cheatsheet panel.
    """
    bw = x1 - x0
    bh = y1 - y0
    mid_x = x0 + bw // 2
    mid_y = y0 + bh // 2

    # Background card
    card_bg = theme["card_bg"]
    draw.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=card_bg,
                            outline=theme.get("card_border", (210, 210, 210)), width=1)

    # Colour palette (works for both light/dark themes)
    is_dark  = theme["name"] == "dark"
    c_price  = (200, 200, 200) if is_dark else (40, 40, 40)
    c_green  = (22, 160, 60)
    c_red    = (200, 40, 40)
    c_zone   = (255, 165, 0)     # orange — OB/zone
    c_blue   = (30, 130, 220)    # EMA/VWAP
    c_muted  = (120, 120, 120)

    lw = 2   # line width

    # ── Diagram varies by strategy ─────────────────────────────────────────────
    pad = 18
    left  = x0 + pad
    right = x1 - pad
    top   = y0 + pad + 14   # reserve top for label
    bot   = y1 - pad

    def pt(fx: float, fy: float) -> tuple[int, int]:
        """Map 0–1 fractions to pixel coords within the box."""
        return (int(left + fx * (right - left)), int(top + fy * (bot - top)))

    sid = strategy_id

    if sid in ("order_block", "breaker_block"):
        # Impulse down → OB zone → retest → bounce up
        pts = [pt(0,0.3), pt(0.25,0.65), pt(0.45,0.68), pt(0.6,0.55),
               pt(0.75,0.52), pt(1.0,0.2)]
        draw.line(pts, fill=c_price, width=lw)
        # OB zone rectangle
        oz_t, oz_b = pt(0.22, 0.60)[1], pt(0.22, 0.72)[1]
        draw.rectangle([pt(0.22,0)[0], oz_t, pt(0.60,0)[0], oz_b],
                       fill=(*c_zone[:3], 50) if is_dark else None,
                       outline=c_zone, width=1)
        draw.text(pt(0.24, 0.63), "OB ZONE", font=_font("regular", 14), fill=c_zone)
        # Retest arrow
        ax, ay = pt(0.70, 0.56)
        draw.line([(ax, ay), (ax + 22, ay - 16)], fill=c_green, width=lw + 1)
        draw.text((ax + 4, ay - 30), "RETEST", font=_font("regular", 13), fill=c_green)

    elif sid in ("fvg",):
        # 3-candle impulse gap
        pts = [pt(0,0.6), pt(0.3,0.65), pt(0.5,0.35), pt(0.7,0.38), pt(1.0,0.2)]
        draw.line(pts, fill=c_price, width=lw)
        # FVG band
        gz_t, gz_b = pt(0.28, 0.40)[1], pt(0.28, 0.64)[1]
        draw.rectangle([pt(0.28,0)[0], gz_t, pt(0.52,0)[0], gz_b],
                       outline=c_blue, width=1)
        draw.text(pt(0.30, 0.52), "FVG", font=_font("regular", 14), fill=c_blue)

    elif sid == "liquidity_grab":
        # Equal highs swept → reversal
        pts = [pt(0,0.5), pt(0.2,0.3), pt(0.4,0.32), pt(0.55,0.12),
               pt(0.65,0.35), pt(1.0,0.7)]
        draw.line(pts, fill=c_price, width=lw)
        # Equal highs line
        eh_y = pt(0, 0.30)[1]
        draw.line([pt(0.15,0)[0], eh_y, pt(0.60,0)[0], eh_y],
                  fill=c_red, width=1)
        draw.text(pt(0.16, 0.27), "EQUAL HIGHS", font=_font("regular", 13), fill=c_red)
        # Sweep wick
        draw.line([pt(0.55,0)[0], pt(0,0.30)[1], pt(0.55,0)[0], pt(0,0.10)[1]],
                  fill=c_red, width=lw)
        draw.text(pt(0.57, 0.10), "SWEEP", font=_font("regular", 13), fill=c_red)

    elif sid in ("trend_pullback",):
        # Uptrend → pullback to EMA → bounce
        pts = [pt(0,0.8), pt(0.2,0.55), pt(0.4,0.3), pt(0.55,0.48),
               pt(0.65,0.52), pt(1.0,0.15)]
        draw.line(pts, fill=c_price, width=lw)
        # EMA line
        ema_pts = [pt(0,0.75), pt(0.3,0.50), pt(0.6,0.48), pt(1.0,0.22)]
        draw.line(ema_pts, fill=c_blue, width=1)
        draw.text(pt(0.62, 0.50), "EMA", font=_font("regular", 13), fill=c_blue)
        # Entry arrow
        ax, ay = pt(0.65, 0.52)
        draw.line([(ax, ay + 18), (ax, ay)], fill=c_green, width=lw + 1)
        draw.text((ax - 10, ay + 20), "ENTRY", font=_font("regular", 13), fill=c_green)

    elif sid in ("range_breakout", "consolidation_breakout", "orb"):
        # Range → breakout
        pts = [pt(0,0.5), pt(0.15,0.48), pt(0.3,0.52), pt(0.5,0.49),
               pt(0.65,0.48), pt(0.7,0.3), pt(1.0,0.1)]
        draw.line(pts, fill=c_price, width=lw)
        # Range box
        draw.rectangle([pt(0,0)[0], pt(0,0.44)[1], pt(0.68,0)[0], pt(0,0.56)[1]],
                       outline=c_muted, width=1)
        draw.text(pt(0.05, 0.44), "RANGE", font=_font("regular", 13), fill=c_muted)
        # Breakout arrow
        bx, by = pt(0.70, 0.30)
        draw.line([(bx, by + 20), (bx, by)], fill=c_green, width=lw + 1)
        draw.text((bx - 16, by - 18), "BREAK", font=_font("regular", 13), fill=c_green)

    elif sid in ("sr_flip", "trendline_break"):
        # Support → break → flip → rejection
        pts = [pt(0,0.3), pt(0.3,0.32), pt(0.45,0.55), pt(0.6,0.48),
               pt(0.75,0.52), pt(1.0,0.7)]
        draw.line(pts, fill=c_price, width=lw)
        # Old support (now resistance) line
        sr_y = pt(0, 0.48)[1]
        draw.line([pt(0.3,0)[0], sr_y, pt(1.0,0)[0], sr_y],
                  fill=c_red, width=1)
        draw.text(pt(0.32, 0.50), "OLD S → NEW R", font=_font("regular", 12), fill=c_red)

    elif sid == "vwap_strategy":
        # Price below VWAP → reclaim → rally
        pts = [pt(0,0.65), pt(0.2,0.72), pt(0.4,0.55), pt(0.55,0.48),
               pt(1.0,0.2)]
        draw.line(pts, fill=c_price, width=lw)
        vwap_pts = [pt(0,0.50), pt(0.35,0.52), pt(0.7,0.42), pt(1.0,0.38)]
        draw.line(vwap_pts, fill=c_blue, width=2)
        draw.text(pt(0.72, 0.40), "VWAP", font=_font("regular", 13), fill=c_blue)

    else:
        # Generic: simple uptrend with pullback
        pts = [pt(0,0.7), pt(0.25,0.5), pt(0.45,0.55), pt(0.6,0.4), pt(1.0,0.15)]
        draw.line(pts, fill=c_price, width=lw)

    # ENTRY/SL/TARGET annotation lines
    ex, ey = pt(0.75, 0.40)
    draw.line([(left, ey), (right, ey)], fill=c_green, width=1)
    draw.text((right - 50, ey - 16), "ENTRY", font=_font("regular", 12), fill=c_green)

    # Box label at top
    f_tag = _font("bold", 15)
    draw.text((x0 + pad, y0 + 6), "CHART REFERENCE", font=f_tag, fill=c_muted)


def _generate_cheatsheet_post_body(
    strategy_id: str | None = None,
    theme_name: str | None = None,
) -> str:
    """
    Generate cheatsheet as up to 4 separate part images if content is large.
    Returns path to Part 1. All parts are saved to disk.
    """
    if strategy_id is None or strategy_id not in _CHEATSHEET_DB:
        strategy_id = _random.choice(list(_CHEATSHEET_DB.keys()))

    data  = _CHEATSHEET_DB[strategy_id]
    theme = _pick_theme(theme_name)

    # ── Estimate heights to decide how many pages we need ─────────────────────
    # Use a throwaway image just for measuring
    _tmp_img, _tmp_draw = _theme_canvas(theme)
    f_lbl  = _font("bold", 28)
    f_body = _font("regular", 26)

    # Content slots: (icon, label, data_key, accent_key)
    _SLOTS = [
        ("ENTRY RULES",  data.get("entry", []),  "accent"),
        ("STOP LOSS",    data.get("sl",    ""),   "red"),
        ("TARGET",       data.get("target",""),   "green"),
        ("AVOID THESE",  data.get("avoid", []),   "accent2"),
    ]

    FOOTER_H   = 72 + 12
    HEADER_H   = 210   # themed header
    CONCEPT_H  = 120   # concept band estimate
    DIAGRAM_H  = 240   # chart reference panel
    AVAIL_H    = H - HEADER_H - FOOTER_H   # usable body height per page

    # Measure each section
    slot_heights = []
    for label, content, _ in _SLOTS:
        slot_heights.append(_cheatsheet_section_height(
            _tmp_draw, f_lbl, f_body, label, content, theme))

    # Split slots across pages:
    # Page 1: Concept + diagram + Entry Rules
    # Page 2: SL + Target  (if needed)
    # Page 3: Avoid These  (if needed)
    # We group greedily, never exceeding AVAIL_H per page.
    pages: list[list[int]] = []   # each element = list of slot indices
    current_page: list[int] = []
    current_h = CONCEPT_H + DIAGRAM_H + 24  # page 1 always has concept+diagram

    for i, sh in enumerate(slot_heights):
        if current_h + sh <= AVAIL_H:
            current_page.append(i)
            current_h += sh
        else:
            if current_page:
                pages.append(current_page)
            current_page = [i]
            current_h = sh
    if current_page:
        pages.append(current_page)

    total_parts = len(pages)
    saved_paths: list[str] = []

    for page_idx, slot_indices in enumerate(pages):
        part_num  = page_idx + 1
        part_label = f"PART {part_num}/{total_parts}" if total_parts > 1 else ""

        img, draw = _theme_canvas(theme)
        title = data["title"] + " CHEAT SHEET"
        y = _themed_header(draw, theme, title,
                           subtitle=f"SMC Strategy — {part_label}" if part_label else "SMC Strategy")
        foot_h = FOOTER_H_SUBTLE
        strip_top = H - foot_h - CHART_STRIP_H
        _apply_chart_texture_background(
            img, y, strip_top, mode=theme["name"], base_rgb=theme["bg"], with_grid=False
        )
        _apply_chart_texture_background(
            img, strip_top, H - foot_h, mode=theme["name"], base_rgb=theme["bg"], with_grid=True
        )
        y += 18

        if part_num == 1:
            # ── Concept band ───────────────────────────────────────────────────
            concept = data.get("concept", "")
            if concept:
                f_con = _font("regular", 27)
                wrap_at = _wrap_chars(f_con, INNER_W - 24)
                con_lines = textwrap.wrap(concept, width=wrap_at)
                try:
                    _, _, _, lh = draw.textbbox((0, 0), "Ay", font=f_con)
                except Exception:
                    lh = 27
                con_h = len(con_lines) * (lh + 5) + 20
                draw.rounded_rectangle([MARGIN - 10, y, W - MARGIN + 10, y + con_h],
                                       radius=10, fill=theme["card_bg"])
                cy = y + 10
                for line in con_lines:
                    draw.text((MARGIN + 4, cy), line, font=f_con, fill=theme["muted"])
                    cy += lh + 5
                y = y + con_h + 20

            # ── Real chart strip (candles + SMC overlays, TV-style theme; Yahoo data) ─
            diag_h = min(DIAGRAM_H, H - y - FOOTER_H - sum(slot_heights[i] for i in slot_indices) - 24)
            diag_h = max(160, diag_h)
            x0, y0 = MARGIN, y
            x1, y1 = W - MARGIN, y + diag_h

            thumb = None
            try:
                from content_engine.services.chart_engine import render_cheatsheet_chart_strip
                thumb = render_cheatsheet_chart_strip(
                    strategy_id, width=x1 - x0, height=y1 - y0
                )
            except Exception as e:
                log.warning("Cheatsheet real chart strip: %s", e)

            if thumb is not None:
                img.paste(thumb, (x0, y0), thumb)
                border = theme.get("card_border", (80, 80, 80))
                draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=border, width=2)
                f_tag = _font("bold", 15)
                c_muted = theme.get("muted", (120, 120, 120))
                draw.text(
                    (x0 + 8, y0 + 6),
                    "CHART REFERENCE  (5m · candle chart · live Yahoo data)",
                    font=f_tag,
                    fill=c_muted,
                )
            else:
                _draw_chart_reference(draw, img, strategy_id, theme, x0, y0, x1, y1)
            y += diag_h + 20

        # ── Sections for this page ─────────────────────────────────────────────
        accent_map = {
            "accent":  theme["accent"],
            "red":     theme["red"],
            "green":   theme["green"],
            "accent2": theme["accent2"],
        }

        for slot_i in slot_indices:
            label, content, accent_key = _SLOTS[slot_i]
            accent = accent_map[accent_key]
            draw.text((MARGIN, y), label, font=f_lbl, fill=accent)
            try:
                _, _, _, lh = draw.textbbox((0, 0), label, font=f_lbl)
            except Exception:
                lh = 28
            y += lh + 8

            if isinstance(content, list):
                for item in content:
                    item = item.strip()
                    if not item:
                        continue
                    # Dash bullet (no emoji — PIL font issues)
                    draw.text((MARGIN + 4, y), "-", font=f_body, fill=accent)
                    try:
                        _, _, cw, _ = draw.textbbox((0, 0), "- ", font=f_body)
                    except Exception:
                        cw = 18
                    wrap_at = _wrap_chars(f_body, INNER_W - cw - 8)
                    lines = textwrap.wrap(item, width=wrap_at) or [item]
                    for ln in lines:
                        draw.text((MARGIN + cw + 4, y), ln, font=f_body, fill=theme["fg"])
                        try:
                            _, _, _, vlh = draw.textbbox((0, 0), ln, font=f_body)
                        except Exception:
                            vlh = 26
                        y += vlh + 4
                    y += 2
            else:
                wrap_at = _wrap_chars(f_body, INNER_W)
                for ln in textwrap.wrap(str(content), width=wrap_at) or [str(content)]:
                    draw.text((MARGIN + 4, y), ln, font=f_body, fill=theme["fg"])
                    try:
                        _, _, _, lh2 = draw.textbbox((0, 0), ln, font=f_body)
                    except Exception:
                        lh2 = 26
                    y += lh2 + 4
            y += 14
            _themed_divider(draw, theme, y)
            y += 14

        # ── Logo watermark + footer ────────────────────────────────────────────
        _paste_logo_watermark(img, opacity=0.06)
        _themed_footer(draw, theme, img=img)

        suffix = f"cheatsheet_{strategy_id}_p{part_num}"
        saved_paths.append(_save(img, suffix))

    return saved_paths[0]   # return Part 1 path; all parts are on disk


# ═══════════════════════════════════════════════════════════════════════════════
# POST TYPE 3 — HEADLINE
# Big-text viral card for breaking market news
# ═══════════════════════════════════════════════════════════════════════════════

def generate_headline_post(
    news_item: dict[str, Any],
    theme_name: str | None = None,
) -> str:
    """
    Generate a big-text headline card for a news intelligence item.

    The headline is extracted from the event string using the most
    impactful numbers/keywords. Falls back to the stock category name.

    Args:
        news_item: dict from news_agent (stock, event, impact, trade_idea). Optional
            ``part`` / ``total_parts`` for **PART N OF M** badge; same enrichment keys
            as :func:`generate_news_image`.
        theme_name: "light" | "dark" | None (random)

    Returns:
        Absolute path to saved PNG.
    """
    print(f"[DEBUG] Entered image generator: generate_headline_post(stock={news_item.get('stock', '?')!r})")
    try:
        return _generate_headline_post_body(news_item, theme_name)
    except Exception as e:
        print(f"[ERROR] Image generation failed: {e}")
        raise


def _generate_headline_post_body(
    news_item: dict[str, Any],
    theme_name: str | None = None,
) -> str:
    """Headline card — Part 2 = trade playbook only (carousel)."""
    ex = _enrich_news_item(news_item)
    stock = news_item.get("stock", "MARKET")
    event = news_item.get("event", "")
    impact = news_item.get("impact", "")

    headline = _extract_headline(event, stock)

    theme = _pick_theme(theme_name)
    impact_lower = str(impact).lower()
    if "bullish" in impact_lower:
        direction_colour = theme["green"]
        direction_label = "BULLISH"
    elif "bearish" in impact_lower:
        direction_colour = theme["red"]
        direction_label = "BEARISH"
    else:
        direction_colour = theme["accent"]
        direction_label = "WATCH"

    img, draw = _theme_canvas(theme)
    foot_h = FOOTER_H_SUBTLE
    strip_top = H - foot_h - CHART_STRIP_H
    _apply_chart_texture_background(img, 0, strip_top, mode=theme["name"], base_rgb=theme["bg"], with_grid=False)
    _apply_chart_texture_background(
        img, strip_top, H - foot_h, mode=theme["name"], base_rgb=theme["bg"], with_grid=True
    )

    badge = _news_series_badge_text(news_item)
    if badge:
        _draw_series_badge_themed(draw, theme, badge, y=36)

    part = news_item.get("part")
    y = 44

    hook = _pick_viral_hook()
    f_hook = _font("bold", 52)
    for ln in _wrap_max_words_per_line(hook, 7):
        try:
            _, _, hw, hh = draw.textbbox((0, 0), ln, font=f_hook)
        except Exception:
            hw, hh = INNER_W, 52
        draw.text(((W - hw) // 2, y), ln, font=f_hook, fill=theme["accent"])
        y += hh + 4
    y += 12

    if part == 2:
        f_play = _font("bold", 36)
        try:
            _, _, pw, ph = draw.textbbox((0, 0), "TRADE PLAYBOOK", font=f_play)
        except Exception:
            pw, ph = 280, 40
        draw.text(((W - pw) // 2, y), "TRADE PLAYBOOK", font=f_play, fill=theme["accent2"])
        y += ph + 24

        bullets = ex.get("trade_bullets") or [_truncate_words(ex["trade_line"], 10)]
        bullets = bullets[:4]
        f_b = _font("regular", 28)
        f_num = _font("bold", 28)
        inner_pad = 52
        card_top = y
        sh_col = (55, 60, 80) if theme["name"] == "dark" else (200, 208, 220)
        # Measure height
        est_h = 20
        for b in bullets:
            bln = (_wrap_max_words_per_line(b, 10) or [b])[0]
            try:
                _, _, _, rh = draw.textbbox((0, 0), bln, font=f_b)
            except Exception:
                rh = 30
            est_h += rh + 10
        card_bot = card_top + est_h + 8
        _draw_rounded_shadow_card(
            draw,
            (MARGIN - 10, card_top, W - MARGIN + 10, card_bot),
            fill=theme["card_bg"],
            radius=16,
            shadow_rgb=sh_col,
        )
        row_y = card_top + 16
        for i, b in enumerate(bullets, start=1):
            bln = (_wrap_max_words_per_line(b, 10) or [b])[0]
            draw.text((MARGIN + inner_pad, row_y), f"{i}.", font=f_num, fill=direction_colour)
            try:
                _, _, nw, _ = draw.textbbox((0, 0), f"{i}.", font=f_num)
            except Exception:
                nw = 28
            draw.text((MARGIN + inner_pad + nw + 8, row_y), bln, font=f_b, fill=theme["fg"])
            try:
                _, _, _, rh = draw.textbbox((0, 0), bln, font=f_b)
            except Exception:
                rh = 30
            row_y += rh + 10
        y = card_bot + 20
    else:
        f_tag = _font("bold", 26)
        tag_text = stock.upper()
        try:
            _, _, tw, th = draw.textbbox((0, 0), tag_text, font=f_tag)
        except Exception:
            tw, th = 200, 28
        draw.rounded_rectangle(
            [MARGIN - 2, y - 6, MARGIN + tw + 24, y + th + 6],
            radius=12,
            fill=theme["accent"],
        )
        draw.text((MARGIN + 10, y), tag_text, font=f_tag, fill=(255, 255, 255))
        y += th + 16

        draw.line([(MARGIN, y), (W - MARGIN, y)], fill=theme["accent"], width=3)
        y += 20

        for size in (96, 84, 72, 64, 54):
            f_hl = _font("bold", size)
            try:
                _, _, tw2, th2 = draw.textbbox((0, 0), headline, font=f_hl)
            except Exception:
                tw2, th2 = W, size
            if tw2 <= INNER_W:
                x = (W - tw2) // 2
                draw.text((x, y), headline, font=f_hl, fill=theme["fg"])
                y += th2 + 12
                break
            if size <= 64:
                for ln in _wrap_max_words_per_line(headline, 8):
                    try:
                        _, _, lw, lh = draw.textbbox((0, 0), ln, font=f_hl)
                    except Exception:
                        lw, lh = INNER_W, size
                    draw.text(((W - lw) // 2, y), ln, font=f_hl, fill=theme["fg"])
                    y += lh + 6
                break

        f_dir = _font("bold", 30)
        try:
            _, _, dw, dh = draw.textbbox((0, 0), direction_label, font=f_dir)
        except Exception:
            dw, dh = 120, 32
        dx = (W - dw) // 2
        draw.rounded_rectangle(
            [dx - 16, y - 4, dx + dw + 16, y + dh + 4],
            radius=14,
            fill=direction_colour,
        )
        draw.text((dx, y), direction_label, font=f_dir, fill=(255, 255, 255))
        y += dh + 16

        f_wl = _font("regular", 22)
        wl_text = " · ".join(ex["watchlist"][:5])
        for ln in _wrap_max_words_per_line(wl_text, 10)[:1]:
            try:
                _, _, lw, lh = draw.textbbox((0, 0), ln, font=f_wl)
            except Exception:
                lw, lh = INNER_W, 24
            draw.text(((W - lw) // 2, y), ln, font=f_wl, fill=theme["muted"])
            y += lh + 4
        y += 10
        draw.line([(MARGIN, y), (W - MARGIN, y)], fill=theme["card_border"], width=1)
        y += 14
        for imp_ln in _wrap_max_words_per_line(ex["impact_line"], 10)[:2]:
            f_imp = _font("regular", 26)
            try:
                _, _, iw, ih = draw.textbbox((0, 0), imp_ln, font=f_imp)
            except Exception:
                iw, ih = INNER_W, 28
            draw.text(((W - iw) // 2, y), imp_ln, font=f_imp, fill=theme["fg"])
            y += ih + 4

    f_r = _font("regular", 20)
    _rw = ex["risk_line"].split()
    rr = " ".join(_rw[:14]) + ("…" if len(_rw) > 14 else "")
    if y < strip_top - 40:
        try:
            _, _, rw, rh = draw.textbbox((0, 0), rr, font=f_r)
        except Exception:
            rw, rh = INNER_W, 22
        draw.text(((W - rw) // 2, y), rr, font=f_r, fill=theme["muted"])
        y += rh + 8
    if y < strip_top - 30:
        cta = _pick_micro_cta()
        f_c = _font("bold", 22)
        try:
            _, _, cw, ch = draw.textbbox((0, 0), cta, font=f_c)
        except Exception:
            cw, ch = INNER_W, 24
        draw.text(((W - cw) // 2, y), cta, font=f_c, fill=theme["accent2"])

    _themed_footer(draw, theme, img=img)

    part = news_item.get("part")
    tot = news_item.get("total_parts")
    if part is not None and tot is not None:
        try:
            return _save(img, f"headline_p{int(part)}of{int(tot)}")
        except (TypeError, ValueError):
            pass
    return _save(img, "headline")


def _extract_headline(event: str, fallback: str) -> str:
    """
    Pull the most punchy short phrase from an event string.
    Priority: numbers with % or ₹ → standalone numbers → first few words.
    """
    # Try ₹ or % patterns first
    m = re.search(r"([\d,]+(?:\.\d+)?%?)\s*(fall|rise|surge|drop|jump|gain|cut|hike|up|down)", event, re.I)
    if m:
        val  = m.group(1).replace(",", "")
        verb = m.group(2).upper()
        return f"{val}% {verb}" if "%" not in val else f"{val} {verb}"

    m2 = re.search(r"(₹[\d,]+(?:\.\d+)?(?:\s*(?:cr|lakh|billion|trillion))?)", event, re.I)
    if m2:
        return m2.group(1).upper()

    # Extract company + short action
    words = event.split()[:6]
    short = " ".join(words).rstrip(".,:")
    return short.upper() if short else fallback.upper()


# ═══════════════════════════════════════════════════════════════════════════════
# POST TYPE 4 — QUICK TIPS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_quick_tips_post(
    tips_index: int | None = None,
    theme_name: str | None = None,
) -> str:
    """
    Generate a quick-tips educational card.

    Args:
        tips_index: index into _TIPS_DB (random if None)
        theme_name: "light" | "dark" | None (random)

    Returns:
        Absolute path to saved PNG.
    """
    print(f"[DEBUG] Entered image generator: generate_quick_tips_post(tips_index={tips_index!r})")
    try:
        return _generate_quick_tips_post_body(tips_index, theme_name)
    except Exception as e:
        print(f"[ERROR] Image generation failed: {e}")
        raise


def _generate_quick_tips_post_body(
    tips_index: int | None = None,
    theme_name: str | None = None,
) -> str:
    idx = tips_index if (tips_index is not None and 0 <= tips_index < len(_TIPS_DB)) \
          else _random.randint(0, len(_TIPS_DB) - 1)

    tips_data = _TIPS_DB[idx]
    theme     = _pick_theme(theme_name)
    accent    = _TIPS_ACCENT.get(tips_data.get("colour", "blue"), theme["accent"])

    img, draw = _theme_canvas(theme)

    y = _themed_header(draw, theme, tips_data["title"], subtitle="Daily Tip")
    foot_h = FOOTER_H_SUBTLE
    strip_top = H - foot_h - CHART_STRIP_H
    _apply_chart_texture_background(img, y, strip_top, mode=theme["name"], base_rgb=theme["bg"], with_grid=False)
    _apply_chart_texture_background(
        img, strip_top, H - foot_h, mode=theme["name"], base_rgb=theme["bg"], with_grid=True
    )
    y += 16

    hook = _pick_viral_hook()
    f_hook = _font("bold", 46)
    for ln in _wrap_max_words_per_line(hook, 8):
        try:
            _, _, hw, hh = draw.textbbox((0, 0), ln, font=f_hook)
        except Exception:
            hw, hh = INNER_W, 46
        draw.text(((W - hw) // 2, y), ln, font=f_hook, fill=theme["accent"])
        y += hh + 4
    y += 8

    subtitle = tips_data.get("subtitle", "")
    if subtitle:
        f_sub = _font("regular", 26)
        sub_short = _truncate_words(subtitle, 12)
        try:
            _, _, sw, sh = draw.textbbox((0, 0), sub_short, font=f_sub)
        except Exception:
            sw, sh = INNER_W, 26
        draw.text(((W - sw) // 2, y), sub_short, font=f_sub, fill=theme["muted"])
        y += sh + 20

    _themed_divider(draw, theme, y)
    y += 18

    tips = tips_data.get("tips", [])[:4]
    f_num = _font("bold", 44)
    f_tip = _font("regular", 26)
    card_gap = 14
    for i, tip in enumerate(tips, start=1):
        tip_short = _truncate_words(tip, 10)
        try:
            _, _, _, lh = draw.textbbox((0, 0), tip_short, font=f_tip)
        except Exception:
            lh = 28
        card_h = lh + 36
        sh_rgb = (55, 60, 80) if theme["name"] == "dark" else (190, 196, 208)
        _draw_rounded_shadow_card(
            draw,
            (MARGIN - 10, y, W - MARGIN + 10, y + card_h),
            fill=theme["card_bg"],
            radius=14,
            shadow_rgb=sh_rgb,
        )
        draw.rectangle([MARGIN - 10, y, MARGIN, y + card_h], fill=accent)
        num_str = str(i)
        try:
            _, _, nw, nh = draw.textbbox((0, 0), num_str, font=f_num)
        except Exception:
            nw, nh = 32, 44
        draw.text((MARGIN + 10, y + (card_h - nh) // 2), num_str, font=f_num, fill=accent)
        # Highlight SL in red when present
        tx = MARGIN + nw + 28
        parts = re.split(r"\b(SL|ENTRY|TARGET)\b", tip_short, flags=re.I)
        ox = tx
        for p in parts:
            if not p:
                continue
            up = p.upper()
            col = theme["fg"]
            f_part = _font("bold", 26) if up in ("SL", "ENTRY", "TARGET") else f_tip
            if up == "SL":
                col = C_SL_HI
            elif up == "ENTRY":
                col = C_ENTRY_HI
            elif up == "TARGET":
                col = C_TARGET_HI
            draw.text((ox, y + 10), p, font=f_part, fill=col)
            try:
                _, _, pw2, _ = draw.textbbox((0, 0), p, font=f_part)
            except Exception:
                pw2 = len(p) * 12
            ox += pw2
        y += card_h + card_gap
        if y > strip_top - 40:
            break

    f_cta = _font("bold", 22)
    cta = _pick_micro_cta()
    try:
        _, _, cw, ch = draw.textbbox((0, 0), cta, font=f_cta)
    except Exception:
        cw, ch = INNER_W, 24
    if y < strip_top - 30:
        draw.text(((W - cw) // 2, min(y + 8, strip_top - 28)), cta, font=f_cta, fill=theme["accent2"])

    _themed_footer(draw, theme, img=img)
    return _save(img, "tips")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5+7 — RANDOMIZATION ENGINE & UNIFIED DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════

# Weights: real_chart 40%, diagram 30%, cheatsheet 20%, headline/tips 10%
_POST_TYPE_WEIGHTS = [
    ("real_chart", 0.40),
    ("diagram",    0.30),
    ("cheatsheet", 0.20),
    ("headline",   0.07),
    ("tips",       0.03),
]


def pick_random_post_type() -> str:
    """
    Return a post type based on configured weights.
    Returns one of: 'real_chart' | 'diagram' | 'cheatsheet' | 'headline' | 'tips'
    """
    types   = [t for t, _ in _POST_TYPE_WEIGHTS]
    weights = [w for _, w in _POST_TYPE_WEIGHTS]
    return _random.choices(types, weights=weights, k=1)[0]


def generate_post_for_strategy(
    post: dict[str, Any],
    force_type: str | None = None,
    theme_name: str | None = None,
    *,
    chart_symbol: str | None = None,
    randomize_chart_history: bool = True,
    randomize_instrument: bool = True,
) -> str:
    """
    Unified dispatcher — generates a strategy post in a randomly selected style.

    Args:
        post:       strategy post dict from strategy_agent.generate_strategy_posts()
        force_type: override the random choice ("diagram"|"cheatsheet"|"text")
        theme_name: "light"|"dark"|None
        chart_symbol: optional Yahoo ticker for **real_chart** (e.g. ``^NSEI``). If
            omitted, a random liquid symbol is used each call unless env
            ``CONTENT_CHART_SYMBOL`` is set.
        randomize_chart_history: for **real_chart**, use a random slice of recent
            5m history so levels/candles change between runs.
        randomize_instrument: when ``chart_symbol`` is None, pick a random symbol
            from the chart pool (ignored if you pass ``chart_symbol``).

    Returns:
        File path to saved PNG.
    """
    post_type = force_type or pick_random_post_type()
    print(f"[DEBUG] Entered image generator: generate_post_for_strategy(title={post.get('title', '?')!r}, type={post_type!r})")
    try:
        # Map post title back to strategy_id for diagram/cheatsheet/chart
        raw_title = re.sub(r"^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\s]+", "", post.get("title", ""))
        strategy_id = raw_title.lower().replace(" ", "_").replace("(", "").replace(")", "")
        _ID_ALIASES = {
            "order_block_ob": "order_block",
            "fair_value_gap_fvg": "fvg",
            "liquidity_grab_stop_hunt": "liquidity_grab",
            "vwap_reclaim_rejection": "vwap_strategy",
            "opening_range_breakout_orb": "orb",
            "support_→_resistance_flip": "sr_flip",
            "trendline_break_&_retest": "trendline_break",
            "consolidation_breakout_flag/pennant": "consolidation_breakout",
            "momentum_scalping": "momentum_scalp",
        }
        # Also use strategy_id from the post dict directly if available
        strategy_id = post.get("strategy_id") or _ID_ALIASES.get(strategy_id, strategy_id)
        print(f"[DEBUG] Resolved strategy_id={strategy_id!r} post_type={post_type!r}")

        # ── real_chart (highest priority) ─────────────────────────────────────
        if post_type == "real_chart":
            try:
                from content_engine.services.chart_engine import create_real_chart_post
                strategy_dict = {
                    "id":      strategy_id,
                    "name":    raw_title,
                    "concept": _extract_concept_from_post(post),
                }
                return create_real_chart_post(
                    strategy_dict,
                    symbol=chart_symbol,
                    randomize_instrument=randomize_instrument,
                    randomize_history_window=randomize_chart_history,
                )
            except Exception as chart_exc:
                log.warning("Real chart failed (%s) — falling back to diagram", chart_exc)
                print(f"[DEBUG] Chart fallback triggered: {chart_exc}")
                post_type = "diagram"   # fall through to diagram

        if post_type == "diagram" and strategy_id in _DIAGRAM_MAP:
            return create_visual_strategy_diagram(strategy_id)

        if post_type == "cheatsheet" and strategy_id in _CHEATSHEET_DB:
            return generate_cheatsheet_post(strategy_id, theme_name=theme_name)

        if post_type == "tips":
            return generate_quick_tips_post(theme_name=theme_name)

        # Final fallback — text card (always works)
        return generate_strategy_image(post)
    except Exception as e:
        print(f"[ERROR] Image generation failed: {e}")
        raise


def _extract_concept_from_post(post: dict) -> str:
    """Pull the concept sentence from the post content string."""
    content = post.get("content", "")
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith(("•", "📍", "🛑", "🎯", "📈", "📌", "#", "*")):
            if len(line) > 20:
                return line[:140]
    return post.get("title", "")[:80]


# ═══════════════════════════════════════════════════════════════════════════════
# PART 8 — TEST RUNNER
# python -m content_engine.services.image_generator
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=" * 65)
    print("  IMAGE GENERATOR — Full Test Suite")
    print("=" * 65)

    # ── 1. Strategy text card ──────────────────────────────────────────────────
    sample_strategy = {
        "title": "ORDER BLOCK",
        "tags": ["SMC", "Intraday", "High Probability"],
        "content": (
            "🟦 *STRATEGY: ORDER BLOCK (OB)*\n\n"
            "🧠 *Concept:*\n"
            "Institutions place large orders leaving imbalanced candles; "
            "price returns to these zones before continuing the trend.\n\n"
            "📍 Entry Rules:\n"
            "• Look for a strong rejection candle at the zone\n"
            "• Wait for price to return to the OB zone (50–100% retracement)\n"
            "• Enter on close of the rejection candle or next candle open\n"
            "• Confluence with FVG adds high probability\n\n"
            "🛑 Stop Loss:\n"
            "Below the OB low — typically 5–10 points on NIFTY\n\n"
            "🎯 Profit Target:\n"
            "2R minimum; next liquidity pool or previous swing high/low\n\n"
            "📈 Best Market:\n"
            "Trending market with clear BOS/CHoCH on 15-min or 1-hr chart\n\n"
            "⚖️ Risk Level: Medium\n\n"
            "📌 Always confirm on HTF before entry."
        ),
    }
    p1 = generate_strategy_image(sample_strategy)
    print(f"\n[OK] Strategy text card  : {p1.split(chr(92))[-1]}")

    # ── 2. News intelligence card ──────────────────────────────────────────────
    sample_news = {
        "stock": "EARNINGS BEAT",
        "event": "HDFC Bank Q3 net profit surges 18% YoY, beats analyst estimates on NIM expansion",
        "impact": "Bullish — strong NIM and low NPAs signal continued institutional accumulation",
        "trade_idea": "Watch HDFCBANK above 1720 for breakout; BANKNIFTY likely positive bias today",
        "confidence": "high",
        "source": "Economic Times",
    }
    p2 = generate_news_image(sample_news)
    print(f"[OK] News intel card     : {p2.split(chr(92))[-1]}")

    # ── 3. Cheatsheet cards (light + dark) ────────────────────────────────────
    p3a = generate_cheatsheet_post("order_block", theme_name="light")
    print(f"[OK] Cheatsheet (light)  : {p3a.split(chr(92))[-1]}")

    p3b = generate_cheatsheet_post("fvg", theme_name="dark")
    print(f"[OK] Cheatsheet (dark)   : {p3b.split(chr(92))[-1]}")

    p3c = generate_cheatsheet_post("momentum_scalp", theme_name="dark")
    print(f"[OK] Cheatsheet (dark2)  : {p3c.split(chr(92))[-1]}")

    # ── 4. Headline cards ─────────────────────────────────────────────────────
    p4a = generate_headline_post(sample_news, theme_name="light")
    print(f"[OK] Headline (light)    : {p4a.split(chr(92))[-1]}")

    sample_news_bearish = {
        "stock": "RBI RATE HIKE",
        "event": "RBI hikes repo rate 25bps to 6.75%, signals hawkish stance ahead",
        "impact": "Bearish for banking and real estate; INR strengthens short-term",
        "trade_idea": "Watch BANKNIFTY for breakdown below support; avoid fresh longs",
        "confidence": "high",
        "source": "Bloomberg",
    }
    p4b = generate_headline_post(sample_news_bearish, theme_name="dark")
    print(f"[OK] Headline (dark)     : {p4b.split(chr(92))[-1]}")

    # ── 5. Quick tips cards (all 7 tips) ─────────────────────────────────────
    print("\nGenerating all tips cards...")
    for i in range(len(_TIPS_DB)):
        theme_choice = "dark" if i % 2 == 0 else "light"
        pt = generate_quick_tips_post(tips_index=i, theme_name=theme_choice)
        print(f"[OK] Tips #{i+1} ({theme_choice:<5}) : {pt.split(chr(92))[-1]}")

    # ── 6. All diagram templates ───────────────────────────────────────────────
    print("\nGenerating all diagram templates...")
    for name in _DIAGRAM_MAP.keys():
        try:
            pd = create_visual_strategy_diagram(name)
            print(f"[OK] diagram:{name:<28} -> {pd.split(chr(92))[-1]}")
        except Exception as e:
            print(f"[ERR] diagram:{name}: {e}")

    # ── 7. Unified dispatcher demo ────────────────────────────────────────────
    print("\nTesting unified dispatcher (random types)...")
    for force in ["diagram", "cheatsheet", "tips", "text"]:
        try:
            pu = generate_post_for_strategy(sample_strategy, force_type=force)
            print(f"[OK] dispatcher:{force:<12} -> {pu.split(chr(92))[-1]}")
        except Exception as e:
            print(f"[ERR] dispatcher:{force}: {e}")

    print()
    print(f"All images saved in: {_OUT_DIR}")
    print("=" * 65)
