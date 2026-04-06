"""
Content Creation / agents / design_agent.py

DesignAgent V2 -- renders carousel slides to 1080x1080 premium PNG images.

V2 FIX: FILL ALL SPACE. No slide should have > 20% empty canvas.

Design System:
  - Background: deep dark gradient (#0a0a0a -> #111111)
  - Green: #00e676 (bullish)   Red: #ff1744 (bearish)
  - Yellow: #ffd600 (important/highlight)
  - Accent: per-slide based on sentiment
  - Text: white #ffffff, secondary #888888
  - Cards fill vertically, tight spacing, insight sub-lines
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from agents.base import BaseContentAgent
from config.settings import OUTPUT_DIR
from models.contracts import CarouselContent, DesignOutput, Slide, SlideType

log = logging.getLogger("content_creation.agents.design")

# ═══════════════════════════════════════════════════════════════════════════
#  CONSTANTS — Premium Design System (Off-White / Light Theme)
# ═══════════════════════════════════════════════════════════════════════════

W, H = 1080, 1080
FOOTER_H = 80   # footer bar image height
CONTENT_H = H - FOOTER_H  # usable canvas = 1000px
BG = "#F5F3EF"               # warm off-white
BG_CARD = "#FFFFFF"           # pure white cards
BG_ROW = "#F0EDE8"            # light warm gray rows
COLOR_WHITE = "#1A1A2E"       # near-black for primary text
COLOR_SECONDARY = "#6B7280"   # muted gray for secondary text
COLOR_DIVIDER = "#E5E2DC"     # soft warm divider
PADDING = 60
INNER = 80  # inner padding for card content

# Premium font paths (Poppins — geometric sans-serif)
_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_FILES = {
    "bold":     _FONTS_DIR / "Poppins-Bold.ttf",
    "semibold": _FONTS_DIR / "Poppins-SemiBold.ttf",
    "medium":   _FONTS_DIR / "Poppins-Medium.ttf",
    "regular":  _FONTS_DIR / "Poppins-Regular.ttf",
}
# Fallback system font names if TTF files are missing
_FONT_NAMES = ("Segoe UI Bold", "Segoe UI", "Arial Bold", "Arial",
               "DejaVu Sans Bold", "DejaVu Sans")
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Resolve a premium Poppins TTF font with caching."""
    key = ("bold" if bold else "regular", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    # Try bundled Poppins TTFs first
    ttf_key = "bold" if bold else "regular"
    ttf_path = _FONT_FILES.get(ttf_key)
    if ttf_path and ttf_path.exists():
        try:
            ft = ImageFont.truetype(str(ttf_path), size)
            _FONT_CACHE[key] = ft
            return ft
        except OSError:
            pass

    # Fallback to system fonts
    prefer = _FONT_NAMES if bold else _FONT_NAMES[1::2] + _FONT_NAMES[::2]
    for name in prefer:
        try:
            ft = ImageFont.truetype(name, size)
            _FONT_CACHE[key] = ft
            return ft
        except OSError:
            continue

    ft = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = ft
    return ft


def _font_medium(size: int) -> ImageFont.FreeTypeFont:
    """Medium weight font for secondary text."""
    key = ("medium", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    ttf_path = _FONT_FILES.get("medium")
    if ttf_path and ttf_path.exists():
        try:
            ft = ImageFont.truetype(str(ttf_path), size)
            _FONT_CACHE[key] = ft
            return ft
        except OSError:
            pass
    return _font(size, bold=False)


def _font_semibold(size: int) -> ImageFont.FreeTypeFont:
    """Semi-bold weight font for mid-emphasis text."""
    key = ("semibold", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    ttf_path = _FONT_FILES.get("semibold")
    if ttf_path and ttf_path.exists():
        try:
            ft = ImageFont.truetype(str(ttf_path), size)
            _FONT_CACHE[key] = ft
            return ft
        except OSError:
            pass
    return _font(size, bold=True)


# Accent palette for light theme
ACCENT_PRIMARY = "#2D3561"     # deep indigo — headlines, brands
ACCENT_BULLISH = "#059669"     # emerald green
ACCENT_BEARISH = "#DC2626"     # crimson red
ACCENT_WARN = "#D97706"        # amber
ACCENT_HIGHLIGHT = "#7C3AED"   # violet for special callouts


def _hex(color: str) -> str:
    """Ensure colour string is valid hex."""
    if color and color.startswith("#") and len(color) in (4, 7, 9):
        return color
    return COLOR_WHITE


# ═══════════════════════════════════════════════════════════════════════════
#  BRAND ASSETS (loaded once, cached)
# ═══════════════════════════════════════════════════════════════════════════

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_LOGO_CACHE: Image.Image | None = None
_FOOTER_CACHE: Image.Image | None = None
LOGO_SIZE = 110  # logo overlay size in pixels


def _load_logo() -> Image.Image | None:
    """Load and cache the brand logo (RGBA, resized). Kept as-is — no alpha edit."""
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    logo_path = _ASSETS_DIR / "brand_logo.png"
    if not logo_path.exists():
        log.warning("Brand logo not found: %s", logo_path)
        return None
    logo = Image.open(logo_path).convert("RGBA")
    logo = logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    _LOGO_CACHE = logo
    return _LOGO_CACHE


def _load_footer() -> Image.Image | None:
    """Load and cache the footer bar image."""
    global _FOOTER_CACHE
    if _FOOTER_CACHE is not None:
        return _FOOTER_CACHE
    footer_path = _ASSETS_DIR / "footer_bar.png"
    if not footer_path.exists():
        log.warning("Footer bar not found: %s", footer_path)
        return None
    footer = Image.open(footer_path).convert("RGB")
    footer = footer.resize((W, FOOTER_H), Image.LANCZOS)
    _FOOTER_CACHE = footer
    return _FOOTER_CACHE


def _apply_branding(img: Image.Image, slide_number: int | None = None) -> Image.Image:
    """Overlay brand logo, footer bar, and optional carousel slide number."""
    footer = _load_footer()
    if footer:
        img.paste(footer, (0, CONTENT_H))
    logo = _load_logo()
    if logo:
        margin = 20
        x = W - LOGO_SIZE - margin
        y = margin
        img.paste(logo, (x, y), mask=logo)
    if slide_number is not None:
        draw = ImageDraw.Draw(img)
        badge_font = _font(28, bold=True)
        badge_text = str(slide_number)
        text_bbox = badge_font.getbbox(badge_text)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        pad_x = 18
        pad_y = 10
        badge_x = 24
        badge_y = 22
        badge_w = text_w + pad_x * 2
        badge_h = text_h + pad_y * 2
        draw.rounded_rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            radius=16,
            fill=(255, 255, 255),
            outline=(210, 205, 195),
            width=1,
        )
        draw.text(
            (badge_x + pad_x, badge_y + pad_y - 2),
            badge_text,
            fill=(26, 26, 46),
            font=badge_font,
        )
    return img


def _gradient_bg(draw: ImageDraw.ImageDraw, accent: str = "") -> None:
    """Draw premium off-white gradient with optional accent stripe at top."""
    # Warm off-white gradient: #F5F3EF → #EDE9E3
    for y in range(H):
        t = y / H
        r = int(245 - 8 * t)
        g = int(243 - 10 * t)
        b = int(239 - 12 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    # Accent stripe at very top (4px)
    if accent:
        ac = _hex(accent)
        ar, ag, ab = int(ac[1:3], 16), int(ac[3:5], 16), int(ac[5:7], 16)
        for y in range(4):
            draw.line([(0, y), (W, y)], fill=(ar, ag, ab))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


# ═══════════════════════════════════════════════════════════════════════════
#  DESIGN AGENT
# ═══════════════════════════════════════════════════════════════════════════


class DesignAgent(BaseContentAgent):
    name = "DesignAgent"
    description = "Renders carousel slides to 1080x1080 PNG images using PIL"

    def run(self, *, carousel: CarouselContent) -> DesignOutput:
        output_dir = OUTPUT_DIR / carousel.date
        output_dir.mkdir(parents=True, exist_ok=True)

        slide_paths: list[str] = []

        for slide in carousel.slides:
            img = self._render_slide(slide)
            filename = f"slide_{slide.slide_number:02d}_{slide.slide_type.value}.png"
            out_path = output_dir / filename
            img.save(str(out_path), "PNG", optimize=True)
            slide_paths.append(str(out_path))
            log.info("Rendered slide %d -> %s", slide.slide_number, filename)

        result = DesignOutput(
            slide_paths=slide_paths,
            thumbnail_path=slide_paths[0] if slide_paths else "",
            total_slides=len(slide_paths),
        )
        log.info("DesignAgent rendered %d slides to %s", len(slide_paths), output_dir)
        return result

    # ──────────────────────────────────────────────────────────────────────
    #  DISPATCH
    # ──────────────────────────────────────────────────────────────────────

    def _render_slide(self, slide: Slide) -> Image.Image:
        dispatch = {
            SlideType.COVER: self._draw_cover,
            SlideType.DATA: self._draw_data,
            SlideType.TABLE: self._draw_data,
            SlideType.STOCK_CARD: self._draw_stock,
            SlideType.OUTLOOK: self._draw_outlook,
            SlideType.CTA: self._draw_cta,
            SlideType.DISCLAIMER: self._draw_cta,
            SlideType.NEWS: self._draw_news,
            SlideType.DRIVERS_SENTIMENT: self._draw_drivers_sentiment,
            SlideType.INSIGHT_CTA: self._draw_insight_cta,
        }
        drawer = dispatch.get(slide.slide_type, self._draw_data)
        img = drawer(slide)
        return _apply_branding(img, slide.slide_number)

    # ──────────────────────────────────────────────────────────────────────
    #  COVER SLIDE — Vertically centered, subtitle, filled space
    # ──────────────────────────────────────────────────────────────────────

    def _draw_cover(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        # Thick accent bar at top
        draw.rectangle([(0, 0), (W, 6)], fill=accent)

        # Calculate total block height for vertical centering
        font_h = _font(80, bold=True)
        font_sub = _font(40, bold=True)
        font_date = _font(34)

        headline_lines = _wrap_text(slide.headline, font_h, W - 2 * INNER)
        headline_h = len(headline_lines) * 100
        subtitle_text = slide.body or ""
        date_text = slide.subheadline or ""

        total_block_h = headline_h + 40  # headline + gap
        if subtitle_text:
            total_block_h += 60  # subtitle line
        total_block_h += 40  # gap before accent line
        total_block_h += 20  # accent line
        if date_text:
            total_block_h += 60  # gap + date

        # Center the block vertically in content area
        start_y = max(120, (CONTENT_H - total_block_h) // 2)

        # Subtitle above headline (smaller, secondary)
        if subtitle_text:
            bbox = font_sub.getbbox(subtitle_text)
            sw = bbox[2] - bbox[0]
            draw.text(((W - sw) // 2, start_y), subtitle_text,
                      fill=COLOR_SECONDARY, font=font_sub)
            start_y += 60

        # Headline (big, deep indigo)
        for line in headline_lines:
            bbox = font_h.getbbox(line)
            lw = bbox[2] - bbox[0]
            draw.text(((W - lw) // 2, start_y), line, fill=ACCENT_PRIMARY, font=font_h)
            start_y += 100

        # Accent line (wide)
        start_y += 40
        line_w = 400
        draw.line([((W - line_w) // 2, start_y), ((W + line_w) // 2, start_y)],
                  fill=accent, width=3)

        # Date below accent line
        if date_text:
            start_y += 40
            bbox = font_date.getbbox(date_text)
            dw = bbox[2] - bbox[0]
            draw.text(((W - dw) // 2, start_y), date_text,
                      fill=COLOR_SECONDARY, font=font_date)

        # NIFTY value below date (from sections[0] if present)
        if slide.sections:
            nifty_sec = slide.sections[0]
            if nifty_sec.value:
                start_y += 60
                font_nifty = _font(32, bold=True)
                nifty_val = nifty_sec.value
                bbox = font_nifty.getbbox(nifty_val)
                nw = bbox[2] - bbox[0]
                # Color based on positive/negative
                n_color = ACCENT_BEARISH if "-" in nifty_val else ACCENT_BULLISH
                draw.text(((W - nw) // 2, start_y), nifty_val,
                          fill=n_color, font=font_nifty)
                start_y += 50

        # NIFTY intraday candlestick chart (from data_points if present)
        chart_path = None
        for dp in slide.data_points:
            if dp.get("chart_path"):
                chart_path = dp["chart_path"]
                break
        if chart_path and Path(chart_path).exists():
            try:
                chart_img = Image.open(chart_path).convert("RGBA")
                chart_area_top = start_y + 20
                chart_area_h = CONTENT_H - chart_area_top - 80  # leave room for brand
                if chart_area_h > 100:
                    chart_w = W - 2 * PADDING
                    chart_img = chart_img.resize(
                        (chart_w, chart_area_h), Image.LANCZOS
                    )
                    img.paste(chart_img, (PADDING, chart_area_top), mask=chart_img)
                    draw = ImageDraw.Draw(img)  # refresh draw after paste
            except Exception:
                pass

        # Brand text at bottom
        font_brand = _font(28, bold=True)
        brand = "@StocksWithGaurav"
        bbox = font_brand.getbbox(brand)
        bw = bbox[2] - bbox[0]
        draw.text(((W - bw) // 2, CONTENT_H - 60), brand,
                  fill=COLOR_SECONDARY, font=font_brand)

        return img

    # ──────────────────────────────────────────────────────────────────────
    #  DATA SLIDE — Snapshot / Global / Sentiment (rows with insights)
    #  V2: split value on "|" to render insight sub-line
    # ──────────────────────────────────────────────────────────────────────

    def _draw_data(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 4)], fill=accent)
        y = PADDING + 10

        # Headline
        font_h = _font(44, bold=True)
        draw.text((PADDING, y), slide.headline, fill=ACCENT_PRIMARY, font=font_h)
        y += 62

        draw.line([(PADDING, y), (W - PADDING, y)], fill=COLOR_DIVIDER, width=1)
        y += 24

        # Skip separator rows for card counting
        real_sections = [s for s in slide.sections if s.label != "---"]
        separator_indices = [i for i, s in enumerate(slide.sections) if s.label == "---"]
        n_rows = min(len(real_sections), 8)
        available = CONTENT_H - y - 20
        row_h = min(120, max(70, available // max(n_rows + len(separator_indices), 1)))
        gap = 8

        font_label = _font(28)
        font_val = _font(32, bold=True)
        font_insight = _font(24)

        for i, sec in enumerate(slide.sections[:9]):
            # Separator row (e.g., between global and crypto)
            if sec.label == "---":
                y += 12
                draw.line([(PADDING + 20, y), (W - PADDING - 20, y)],
                          fill=_hex(sec.color), width=2)
                # Separator label centered
                sep_font = _font(22, bold=True)
                sep_bbox = sep_font.getbbox(sec.value)
                sep_w = sep_bbox[2] - sep_bbox[0]
                draw.rounded_rectangle(
                    [((W - sep_w) // 2 - 16, y - 14), ((W + sep_w) // 2 + 16, y + 14)],
                    radius=8, fill=BG,
                )
                draw.text(((W - sep_w) // 2, y - 10), sec.value,
                          fill=_hex(sec.color), font=sep_font)
                y += 28
                continue

            # Split value on "|" for insight sub-line
            parts = sec.value.split("|", 1)
            main_val = parts[0].strip()
            insight = parts[1].strip() if len(parts) > 1 else ""

            # Row card (white card with subtle shadow effect)
            card_h = row_h - gap
            # Shadow (offset rectangle, lighter)
            draw.rounded_rectangle(
                [(PADDING + 2, y + 2), (W - PADDING + 2, y + card_h + 2)],
                radius=14, fill="#E8E5DF",
            )
            draw.rounded_rectangle(
                [(PADDING, y), (W - PADDING, y + card_h)],
                radius=14, fill=BG_CARD,
            )
            # Left accent bar
            draw.rounded_rectangle(
                [(PADDING, y + 4), (PADDING + 5, y + card_h - 4)],
                radius=2, fill=_hex(sec.color),
            )

            if insight:
                # Two-line layout: value top, insight bottom
                val_y = y + 12
                draw.text((PADDING + 24, val_y), sec.label,
                          fill=COLOR_SECONDARY, font=font_label)
                val_bbox = font_val.getbbox(main_val)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 24 - val_w, val_y),
                          main_val, fill=_hex(sec.color), font=font_val)
                # Insight sub-line
                ins_y = val_y + 40
                draw.text((PADDING + 24, ins_y), insight,
                          fill=COLOR_SECONDARY, font=font_insight)
            else:
                # Single-line centered vertically
                label_y = y + (card_h) // 2 - 16
                draw.text((PADDING + 24, label_y), sec.label,
                          fill=COLOR_SECONDARY, font=font_label)
                val_bbox = font_val.getbbox(main_val)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 24 - val_w, label_y),
                          main_val, fill=_hex(sec.color), font=font_val)
            y += row_h

        return img

    # ──────────────────────────────────────────────────────────────────────
    #  STOCK CARD — Symbol, sector, setup, 2-3 WHY bullets, TRADE BIAS
    # ──────────────────────────────────────────────────────────────────────

    def _draw_stock(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 4)], fill=accent)
        y = PADDING

        # "STOCK PICK" tag pill
        font_tag = _font(22, bold=True)
        tag_text = "STOCK PICK"
        tag_bbox = font_tag.getbbox(tag_text)
        tag_w = tag_bbox[2] - tag_bbox[0] + 32
        draw.rounded_rectangle(
            [(PADDING, y), (PADDING + tag_w, y + 36)],
            radius=8, fill=accent,
        )
        draw.text((PADDING + 16, y + 7), tag_text, fill="#ffffff", font=font_tag)
        y += 56

        # Symbol (BIG)
        font_sym = _font(72, bold=True)
        draw.text((PADDING, y), slide.headline, fill=ACCENT_PRIMARY, font=font_sym)
        y += 88

        # Company name
        if slide.subheadline:
            font_name = _font_medium(26)
            draw.text((PADDING, y), slide.subheadline, fill=COLOR_SECONDARY, font=font_name)
            y += 40

        # Accent divider
        draw.line([(PADDING, y), (W - PADDING, y)], fill=accent, width=2)
        y += 24

        # Section rows — render each in its own card
        font_label = _font_medium(22)
        font_val = _font_semibold(28)
        font_why_title = _font_semibold(24)
        font_why_val = _font_medium(26)

        why_started = False

        for sec in slide.sections[:7]:
            label = sec.label.upper()

            if label == "WHY" and not why_started:
                # WHY header
                why_started = True
                y += 12
                draw.text((PADDING, y), "WHY THIS STOCK?", fill=accent, font=font_why_title)
                y += 36

            if label == "WHY":
                # Bullet point
                draw.ellipse([(PADDING + 4, y + 8), (PADDING + 14, y + 18)],
                             fill=accent)
                lines = _wrap_text(sec.value, font_why_val, W - PADDING - 40 - PADDING)
                for line in lines[:2]:
                    draw.text((PADDING + 24, y), line,
                              fill=COLOR_WHITE, font=font_why_val)
                    y += 36
                y += 8
            elif label == "ENTRY":
                # Entry idea — white card with green accent
                entry_h = 48
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + entry_h)],
                    radius=10, fill=BG_CARD,
                    outline=ACCENT_BULLISH, width=2,
                )
                entry_label_font = _font_medium(20)
                draw.text((PADDING + 16, y + 12), "ENTRY",
                          fill=COLOR_SECONDARY, font=entry_label_font)
                entry_val_font = _font_semibold(24)
                val_bbox = entry_val_font.getbbox(sec.value)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 16 - val_w, y + 10),
                          sec.value, fill=ACCENT_BULLISH, font=entry_val_font)
                y += entry_h + 10
            elif label == "STOP LOSS" or label == "SL":
                # Stop loss — white card with red accent
                sl_h = 48
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + sl_h)],
                    radius=10, fill=BG_CARD,
                    outline=ACCENT_BEARISH, width=2,
                )
                sl_label_font = _font_medium(20)
                draw.text((PADDING + 16, y + 12), "STOP LOSS",
                          fill=COLOR_SECONDARY, font=sl_label_font)
                sl_val_font = _font_semibold(24)
                val_bbox = sl_val_font.getbbox(sec.value)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 16 - val_w, y + 10),
                          sec.value, fill=ACCENT_BEARISH, font=sl_val_font)
                y += sl_h + 10
            elif label == "TARGET":
                # Target — white card with blue accent
                tgt_h = 48
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + tgt_h)],
                    radius=10, fill=BG_CARD,
                    outline="#2563EB", width=2,
                )
                tgt_label_font = _font_medium(20)
                draw.text((PADDING + 16, y + 12), "TARGET",
                          fill=COLOR_SECONDARY, font=tgt_label_font)
                tgt_val_font = _font_semibold(24)
                val_bbox = tgt_val_font.getbbox(sec.value)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 16 - val_w, y + 10),
                          sec.value, fill="#2563EB", font=tgt_val_font)
                y += tgt_h + 10
            elif label == "R:R":
                # Risk:Reward — white card with amber accent
                rr_h = 48
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + rr_h)],
                    radius=10, fill=BG_CARD,
                    outline=ACCENT_WARN, width=2,
                )
                rr_label_font = _font_medium(20)
                draw.text((PADDING + 16, y + 12), "R:R",
                          fill=COLOR_SECONDARY, font=rr_label_font)
                rr_val_font = _font_semibold(24)
                val_bbox = rr_val_font.getbbox(sec.value)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 16 - val_w, y + 10),
                          sec.value, fill=ACCENT_WARN, font=rr_val_font)
                y += rr_h + 10
            elif label == "BIAS":
                # Trade bias — highlighted card
                y += 16
                bias_h = 64
                _is_short_bias = sec.color.lower() in ("#ff4444", "#ff0000", "#cc0000", ACCENT_BEARISH.lower())
                bias_bg = "#FEF2F2" if _is_short_bias else "#F0FDF4"
                bias_border = ACCENT_BEARISH if _is_short_bias else ACCENT_BULLISH
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + bias_h)],
                    radius=12, fill=bias_bg,
                    outline=bias_border, width=2,
                )
                bias_label_font = _font_semibold(22)
                draw.text((PADDING + 20, y + 18), "TRADE BIAS",
                          fill=COLOR_SECONDARY, font=bias_label_font)
                val_bbox = font_val.getbbox(sec.value)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((W - PADDING - 20 - val_w, y + 16),
                          sec.value, fill=_hex(sec.color), font=font_val)
                y += bias_h + 16
            else:
                # Regular row (SECTOR, SETUP) — white card
                row_h = 52
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + row_h)],
                    radius=10, fill=BG_CARD,
                )
                draw.text((PADDING + 16, y + 12), label,
                          fill=COLOR_SECONDARY, font=font_label)
                label_bbox = font_label.getbbox(label)
                label_w = label_bbox[2] - label_bbox[0]
                val_x = PADDING + 16 + label_w + 24
                draw.text((val_x, y + 10), sec.value,
                          fill=_hex(sec.color), font=font_val)
                y += row_h + 10

        # If there's a chart image in data_points, render it at the bottom
        chart_path = None
        for dp in slide.data_points:
            if dp.get("chart_path"):
                chart_path = dp["chart_path"]
                break
        if chart_path and Path(chart_path).exists():
            try:
                chart_img = Image.open(chart_path).convert("RGBA")
                chart_area_h = CONTENT_H - y - 20
                if chart_area_h > 120:
                    chart_w = W - 2 * PADDING
                    chart_img = chart_img.resize(
                        (chart_w, chart_area_h), Image.LANCZOS
                    )
                    img.paste(chart_img, (PADDING, y + 10), mask=chart_img)
            except Exception:
                pass
        else:
            # No chart: fill empty space with a styled "CHART UNAVAILABLE" box
            box_top = y + 30
            box_h = max(120, CONTENT_H - box_top - 20)
            draw.rounded_rectangle(
                [(PADDING, box_top), (W - PADDING, box_top + box_h)],
                radius=16, fill=BG_CARD, outline=COLOR_DIVIDER, width=1,
            )
            # Crosshair lines
            mid_x = W // 2
            mid_y = box_top + box_h // 2
            draw.line([(PADDING + 30, mid_y), (W - PADDING - 30, mid_y)],
                      fill=COLOR_DIVIDER, width=1)
            draw.line([(mid_x, box_top + 20), (mid_x, box_top + box_h - 20)],
                      fill=COLOR_DIVIDER, width=1)
            # Text
            font_na = _font(22)
            na_text = "Chart data unavailable"
            na_bbox = font_na.getbbox(na_text)
            na_w = na_bbox[2] - na_bbox[0]
            draw.text(((W - na_w) // 2, mid_y + 20), na_text,
                      fill=COLOR_SECONDARY, font=font_na)

        return img

    # ──────────────────────────────────────────────────────────────────────
    #  OUTLOOK / DRIVERS / INSIGHT SLIDE — numbered cards with sub-lines
    # ──────────────────────────────────────────────────────────────────────

    def _draw_outlook(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 4)], fill=accent)
        y = PADDING + 10

        # Headline
        font_h = _font(44, bold=True)
        draw.text((PADDING, y), slide.headline, fill=ACCENT_PRIMARY, font=font_h)
        y += 62

        draw.line([(PADDING, y), (W - PADDING, y)], fill=COLOR_DIVIDER, width=1)
        y += 36

        # Numbered cards — compact, no wasted space
        font_num = _font(36, bold=True)
        font_title = _font(30, bold=True)
        font_sub = _font(24)
        n_items = min(len(slide.sections), 4)
        available = CONTENT_H - y - 30
        # Tight cards: measure actual content height, cap at 160px
        card_h = min(160, max(100, available // max(n_items, 1)))
        gap = 10

        for sec in slide.sections[:4]:
            # Card background (white card with subtle shadow)
            draw.rounded_rectangle(
                [(PADDING + 2, y + 2), (W - PADDING + 2, y + card_h - gap + 2)],
                radius=16, fill="#E8E5DF",
            )
            draw.rounded_rectangle(
                [(PADDING, y), (W - PADDING, y + card_h - gap)],
                radius=16, fill=BG_CARD,
            )
            # Number circle
            cx = PADDING + 40
            cy = y + 36
            draw.ellipse(
                [(cx - 22, cy - 22), (cx + 22, cy + 22)],
                fill=accent,
            )
            num_text = sec.label.replace(".", "")
            num_bbox = font_num.getbbox(num_text)
            nw = num_bbox[2] - num_bbox[0]
            draw.text((cx - nw // 2, cy - 18), num_text,
                      fill="#ffffff", font=font_num)

            # Split value on newline for title + insight sub-line
            text_x = PADDING + 80
            text_max_w = W - text_x - PADDING - 16
            parts = sec.value.split("\n", 1)
            title = parts[0].strip()
            sub = parts[1].strip() if len(parts) > 1 else ""

            # Title
            title_lines = _wrap_text(title, font_title, text_max_w)
            ty = y + 16
            for line in title_lines[:2]:
                draw.text((text_x, ty), line, fill=COLOR_WHITE, font=font_title)
                ty += 38

            # Insight sub-line (secondary color)
            if sub:
                sub_lines = _wrap_text(sub, font_sub, text_max_w)
                ty += 4
                for line in sub_lines[:2]:
                    draw.text((text_x, ty), line, fill=COLOR_SECONDARY, font=font_sub)
                    ty += 30

            y += card_h

        return img

    # ──────────────────────────────────────────────────────────────────────
    #  NEWS SLIDE — headlines with impact badges and optional thumbnails
    # ──────────────────────────────────────────────────────────────────────

    def _draw_news(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 4)], fill=accent)
        y = PADDING + 10

        # Headline
        font_h = _font(44, bold=True)
        draw.text((PADDING, y), slide.headline, fill=ACCENT_PRIMARY, font=font_h)
        y += 62

        draw.line([(PADDING, y), (W - PADDING, y)], fill=COLOR_DIVIDER, width=1)
        y += 24

        # News cards
        font_impact = _font(18, bold=True)
        font_headline = _font_semibold(26)
        font_source = _font_medium(20)

        n_items = min(len(slide.sections), 3)
        available = CONTENT_H - y - 20
        card_h = min(260, max(180, available // max(n_items, 1)))
        gap = 10

        # Try to download news images
        image_urls = [dp.get("image_url", "") for dp in slide.data_points]

        for i, sec in enumerate(slide.sections[:3]):
            # Card background (white card with shadow)
            draw.rounded_rectangle(
                [(PADDING + 2, y + 2), (W - PADDING + 2, y + card_h - gap + 2)],
                radius=14, fill="#E8E5DF",
            )
            draw.rounded_rectangle(
                [(PADDING, y), (W - PADDING, y + card_h - gap)],
                radius=14, fill=BG_CARD,
            )

            # Left accent bar
            draw.rounded_rectangle(
                [(PADDING, y + 4), (PADDING + 5, y + card_h - gap - 4)],
                radius=2, fill=_hex(sec.color),
            )

            # Try to load thumbnail image
            thumb_w = 0
            img_url = image_urls[i] if i < len(image_urls) else ""
            if img_url:
                try:
                    thumb = self._download_thumbnail(img_url)
                    if thumb:
                        th = card_h - gap - 20
                        tw = th  # square thumbnail
                        thumb = thumb.resize((tw, th), Image.LANCZOS)
                        thumb_x = W - PADDING - 16 - tw
                        img.paste(thumb, (thumb_x, y + 10))
                        # Redraw to refresh
                        draw = ImageDraw.Draw(img)
                        thumb_w = tw + 20
                except Exception:
                    pass

            text_max_w = W - 2 * PADDING - 40 - thumb_w

            # Impact badge
            impact_text = sec.label
            ib = font_impact.getbbox(impact_text)
            iw = ib[2] - ib[0] + 20
            badge_y = y + 12
            draw.rounded_rectangle(
                [(PADDING + 20, badge_y), (PADDING + 20 + iw, badge_y + 26)],
                radius=6, fill=_hex(sec.color),
            )
            draw.text((PADDING + 30, badge_y + 4), impact_text,
                      fill="#ffffff", font=font_impact)

            # Headline text (split on \n for headline + source)
            parts = sec.value.split("\n", 1)
            headline_text = parts[0].strip()
            source_text = parts[1].strip() if len(parts) > 1 else ""

            hl_y = badge_y + 34
            hl_lines = _wrap_text(headline_text, font_headline, text_max_w)
            max_hl_lines = 3
            display_lines = hl_lines[:max_hl_lines]
            # Add ellipsis if headline was truncated
            if len(hl_lines) > max_hl_lines:
                last = display_lines[-1]
                display_lines[-1] = last.rstrip() + "..."
            for line in display_lines:
                draw.text((PADDING + 20, hl_y), line,
                          fill=COLOR_WHITE, font=font_headline)
                hl_y += 34

            # Source
            if source_text:
                draw.text((PADDING + 20, hl_y + 4), source_text,
                          fill=COLOR_SECONDARY, font=font_source)

            y += card_h

        return img

    def _download_thumbnail(self, url: str) -> Image.Image | None:
        """Download a news thumbnail image. Returns PIL Image or None."""
        if not url or not url.startswith("http"):
            return None
        try:
            import httpx as _httpx
            resp = _httpx.get(url, follow_redirects=True, timeout=8)
            if resp.status_code == 200 and len(resp.content) > 1000:
                from io import BytesIO
                return Image.open(BytesIO(resp.content)).convert("RGB")
        except Exception:
            pass
        return None

    # ──────────────────────────────────────────────────────────────────────
    #  CTA SLIDE — Filled with action items + engagement
    # ──────────────────────────────────────────────────────────────────────

    def _draw_cta(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 6)], fill=accent)

        # Calculate layout to vertically center everything
        font_h = _font(52, bold=True)
        font_item = _font(32, bold=True)

        headline_lines = _wrap_text(slide.headline, font_h, W - 2 * INNER)
        n_items = min(len(slide.sections), 4)

        headline_block = len(headline_lines) * 70
        items_block = n_items * 80
        total_block = headline_block + 60 + items_block
        start_y = max(100, (CONTENT_H - total_block) // 2)

        y = start_y

        # Headline centered (deep indigo)
        for line in headline_lines:
            bbox = font_h.getbbox(line)
            lw = bbox[2] - bbox[0]
            draw.text(((W - lw) // 2, y), line, fill=ACCENT_PRIMARY, font=font_h)
            y += 70
        y += 60

        # Action pills (white cards with accent border)
        for sec in slide.sections[:4]:
            pill_h = 62
            # Shadow
            draw.rounded_rectangle(
                [(INNER + 2, y + 2), (W - INNER + 2, y + pill_h + 2)],
                radius=16, fill="#E8E5DF",
            )
            draw.rounded_rectangle(
                [(INNER, y), (W - INNER, y + pill_h)],
                radius=16, fill=BG_CARD, outline=accent, width=2,
            )
            # Number circle
            draw.ellipse(
                [(INNER + 14, y + 11), (INNER + 52, y + 49)],
                fill=accent,
            )
            num_bbox = font_item.getbbox(sec.label)
            nw = num_bbox[2] - num_bbox[0]
            draw.text((INNER + 33 - nw // 2, y + 14), sec.label,
                      fill="#ffffff", font=font_item)
            draw.text((INNER + 70, y + 14), sec.value,
                      fill=ACCENT_PRIMARY, font=font_item)
            y += 80

        # Disclaimer at bottom
        if slide.footer:
            font_disc = _font(20)
            lines = _wrap_text(slide.footer, font_disc, W - 2 * PADDING)
            fy = CONTENT_H - 20 - len(lines) * 28
            for line in lines:
                bbox = font_disc.getbbox(line)
                lw = bbox[2] - bbox[0]
                draw.text(((W - lw) // 2, fy), line,
                          fill=COLOR_SECONDARY, font=font_disc)
                fy += 28

        return img

    # ──────────────────────────────────────────────────────────────────────
    #  DRIVERS + SENTIMENT SLIDE (merged) — compact drivers top,
    #  sentiment strip bottom
    # ──────────────────────────────────────────────────────────────────────

    def _draw_drivers_sentiment(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 4)], fill=accent)
        y = PADDING + 10

        # Headline: "KEY DRIVERS"
        font_h = _font(40, bold=True)
        draw.text((PADDING, y), slide.headline, fill=ACCENT_PRIMARY, font=font_h)
        y += 54

        draw.line([(PADDING, y), (W - PADDING, y)], fill=COLOR_DIVIDER, width=1)
        y += 18

        # ── Compact numbered driver cards (top ~55% of content) ──
        font_num = _font(28, bold=True)
        font_title = _font(24, bold=True)
        font_sub = _font(20)
        n_items = min(len(slide.sections), 4)
        card_h = 110
        gap = 8

        for sec in slide.sections[:4]:
            # White card with shadow
            draw.rounded_rectangle(
                [(PADDING + 2, y + 2), (W - PADDING + 2, y + card_h - gap + 2)],
                radius=14, fill="#E8E5DF",
            )
            draw.rounded_rectangle(
                [(PADDING, y), (W - PADDING, y + card_h - gap)],
                radius=14, fill=BG_CARD,
            )
            # Number circle (smaller)
            cx = PADDING + 34
            cy = y + 30
            draw.ellipse(
                [(cx - 18, cy - 18), (cx + 18, cy + 18)],
                fill=accent,
            )
            num_text = sec.label.replace(".", "")
            num_bbox = font_num.getbbox(num_text)
            nw = num_bbox[2] - num_bbox[0]
            draw.text((cx - nw // 2, cy - 14), num_text,
                      fill="#ffffff", font=font_num)

            # Title + sub-line
            text_x = PADDING + 68
            text_max_w = W - text_x - PADDING - 16
            parts = sec.value.split("\n", 1)
            title = parts[0].strip()
            sub = parts[1].strip() if len(parts) > 1 else ""

            title_lines = _wrap_text(title, font_title, text_max_w)
            ty = y + 14
            for line in title_lines[:2]:
                draw.text((text_x, ty), line, fill=COLOR_WHITE, font=font_title)
                ty += 30
            if sub:
                sub_lines = _wrap_text(sub, font_sub, text_max_w)
                ty += 2
                for line in sub_lines[:2]:
                    draw.text((text_x, ty), line, fill=COLOR_SECONDARY, font=font_sub)
                    ty += 26

            y += card_h

        # ── Sentiment strip (bottom) ──
        y += 12
        # "SENTIMENT" mini-header
        font_strip_h = _font(22, bold=True)
        draw.text((PADDING, y), "MARKET PULSE", fill=ACCENT_PRIMARY, font=font_strip_h)
        y += 30

        # Extract sentiment data from data_points
        sdata = {}
        for dp in slide.data_points:
            if "sentiment_strip" in dp:
                sdata = dp["sentiment_strip"]
                break

        if sdata:
            # Draw 4 compact pill badges in a 2×2 grid
            font_pill_label = _font(18)
            font_pill_val = _font(20, bold=True)
            pill_w = (W - 2 * PADDING - 16) // 2
            pill_h = 50
            pills = [
                ("SENTIMENT", sdata.get("sentiment", "?"), sdata.get("sent_color", accent)),
                ("RISK", sdata.get("risk", "?"), sdata.get("risk_color", accent)),
                ("TREND", sdata.get("trend", "?"), sdata.get("trend_color", accent)),
                ("VIX", sdata.get("vix", "?"), sdata.get("vix_color", accent)),
            ]
            for i, (label, val, color) in enumerate(pills):
                col = i % 2
                row = i // 2
                px = PADDING + col * (pill_w + 16)
                py = y + row * (pill_h + 8)
                # White pill card
                draw.rounded_rectangle(
                    [(px, py), (px + pill_w, py + pill_h)],
                    radius=10, fill=BG_CARD,
                )
                # Left accent bar
                draw.rounded_rectangle(
                    [(px, py + 4), (px + 4, py + pill_h - 4)],
                    radius=2, fill=_hex(color),
                )
                # Label + value
                draw.text((px + 14, py + 6), label,
                          fill=COLOR_SECONDARY, font=font_pill_label)
                vb = font_pill_val.getbbox(val)
                vw = vb[2] - vb[0]
                draw.text((px + pill_w - 10 - vw, py + 14),
                          val, fill=_hex(color), font=font_pill_val)

            y += 2 * (pill_h + 8) + 8

            # Action line at the bottom
            action = sdata.get("action", "")
            if action:
                font_action = _font(18)
                action_lines = _wrap_text(action, font_action, W - 2 * PADDING - 20)
                # Compact action bar
                draw.rounded_rectangle(
                    [(PADDING, y), (W - PADDING, y + 36)],
                    radius=8, fill=BG_ROW,
                )
                if action_lines:
                    draw.text((PADDING + 12, y + 8), action_lines[0],
                              fill=ACCENT_PRIMARY, font=font_action)

        return img

    # ──────────────────────────────────────────────────────────────────────
    #  INSIGHT + CTA SLIDE (merged) — action insights top, CTA bottom
    # ──────────────────────────────────────────────────────────────────────

    def _draw_insight_cta(self, slide: Slide) -> Image.Image:
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        accent = _hex(slide.accent_color)
        _gradient_bg(draw, accent)

        draw.rectangle([(0, 0), (W, 6)], fill=accent)
        y = PADDING + 10

        # Headline: "ACTION PLAN"
        font_h = _font(40, bold=True)
        draw.text((PADDING, y), slide.headline, fill=ACCENT_PRIMARY, font=font_h)
        y += 54

        draw.line([(PADDING, y), (W - PADDING, y)], fill=COLOR_DIVIDER, width=1)
        y += 18

        # ── Compact numbered insight cards (top ~60%) ──
        font_num = _font(26, bold=True)
        font_title = _font(24, bold=True)
        font_sub = _font(20)
        n_items = min(len(slide.sections), 4)
        card_h = 100
        gap = 8

        for sec in slide.sections[:4]:
            # White card with shadow
            draw.rounded_rectangle(
                [(PADDING + 2, y + 2), (W - PADDING + 2, y + card_h - gap + 2)],
                radius=14, fill="#E8E5DF",
            )
            draw.rounded_rectangle(
                [(PADDING, y), (W - PADDING, y + card_h - gap)],
                radius=14, fill=BG_CARD,
            )
            # Number circle
            cx = PADDING + 32
            cy = y + 28
            draw.ellipse(
                [(cx - 17, cy - 17), (cx + 17, cy + 17)],
                fill=accent,
            )
            num_text = sec.label.replace(".", "")
            num_bbox = font_num.getbbox(num_text)
            nw = num_bbox[2] - num_bbox[0]
            draw.text((cx - nw // 2, cy - 13), num_text,
                      fill="#ffffff", font=font_num)

            text_x = PADDING + 66
            text_max_w = W - text_x - PADDING - 16
            parts = sec.value.split("\n", 1)
            title = parts[0].strip()
            sub = parts[1].strip() if len(parts) > 1 else ""

            title_lines = _wrap_text(title, font_title, text_max_w)
            ty = y + 12
            for line in title_lines[:2]:
                draw.text((text_x, ty), line, fill=COLOR_WHITE, font=font_title)
                ty += 30
            if sub:
                sub_lines = _wrap_text(sub, font_sub, text_max_w)
                ty += 2
                for line in sub_lines[:2]:
                    draw.text((text_x, ty), line, fill=COLOR_SECONDARY, font=font_sub)
                    ty += 26

            y += card_h

        # ── CTA section (bottom) ──
        y += 16
        # Accent divider
        draw.line([(PADDING + 40, y), (W - PADDING - 40, y)],
                  fill=accent, width=2)
        y += 16

        # Extract CTA items from data_points
        cta_items = []
        for dp in slide.data_points:
            if dp.get("cta"):
                cta_items = dp.get("cta_items", [])
                break

        font_cta = _font_semibold(24)
        font_cta_num = _font(22, bold=True)
        for i, item in enumerate(cta_items[:3]):
            pill_h = 48
            # White pill with accent outline
            draw.rounded_rectangle(
                [(INNER, y), (W - INNER, y + pill_h)],
                radius=12, fill=BG_CARD, outline=accent, width=2,
            )
            # Number circle
            draw.ellipse(
                [(INNER + 10, y + 8), (INNER + 40, y + 38)],
                fill=accent,
            )
            num_text = str(i + 1)
            nb = font_cta_num.getbbox(num_text)
            nnw = nb[2] - nb[0]
            draw.text((INNER + 25 - nnw // 2, y + 11), num_text,
                      fill="#ffffff", font=font_cta_num)
            draw.text((INNER + 52, y + 11), item,
                      fill=ACCENT_PRIMARY, font=font_cta)
            y += pill_h + 10

        # Disclaimer at bottom
        if slide.footer:
            font_disc = _font(18)
            lines = _wrap_text(slide.footer, font_disc, W - 2 * PADDING)
            fy = CONTENT_H - 16 - len(lines) * 24
            for line in lines:
                bbox = font_disc.getbbox(line)
                lw = bbox[2] - bbox[0]
                draw.text(((W - lw) // 2, fy), line,
                          fill=COLOR_SECONDARY, font=font_disc)
                fy += 24

        return img
