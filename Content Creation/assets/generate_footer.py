"""Generate the footer bar image with proper platform icons and social handles."""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 80
OUT = Path(__file__).parent / "footer_bar.png"

img = Image.new("RGB", (W, H), "#1A1A2E")
draw = ImageDraw.Draw(img)

# Fonts — prefer Poppins from assets, fallback to Segoe
_fonts_dir = Path(__file__).parent / "fonts"
try:
    font    = ImageFont.truetype(str(_fonts_dir / "Poppins-SemiBold.ttf"), 21)
    font_sm = ImageFont.truetype(str(_fonts_dir / "Poppins-Regular.ttf"),  16)
except OSError:
    font    = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 21)
    font_sm = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf",  16)


# ── Icon drawing helpers ───────────────────────────────────────────────────────

def _rounded_rect(d: ImageDraw.ImageDraw, x0, y0, x1, y1, r, fill):
    """Draw a filled rounded rectangle."""
    d.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    d.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    d.ellipse([x0, y0, x0 + 2 * r, y0 + 2 * r], fill=fill)
    d.ellipse([x1 - 2 * r, y0, x1, y0 + 2 * r], fill=fill)
    d.ellipse([x0, y1 - 2 * r, x0 + 2 * r, y1], fill=fill)
    d.ellipse([x1 - 2 * r, y1 - 2 * r, x1, y1], fill=fill)


def draw_instagram(d: ImageDraw.ImageDraw, cx, cy, s=18):
    """Instagram: gradient-ish rounded square + camera ring + dot."""
    # Outer rounded square: approximate IG gradient with pink→purple
    _rounded_rect(d, cx - s, cy - s, cx + s, cy + s, 7, "#C13584")
    # Inner lighter gradient tint (top strip)
    _rounded_rect(d, cx - s, cy - s, cx + s, cy - 2, 7, "#E1306C")
    # Camera lens ring (white outline circle)
    r_ring = int(s * 0.52)
    d.ellipse([cx - r_ring, cy - r_ring, cx + r_ring, cy + r_ring],
              outline="#ffffff", width=3)
    # Viewfinder dot (top-right)
    dot_r = 3
    d.ellipse([cx + s - dot_r * 3, cy - s + dot_r,
               cx + s - dot_r, cy - s + dot_r * 3 + 2],
              fill="#ffffff")


def draw_youtube(d: ImageDraw.ImageDraw, cx, cy, s=18):
    """YouTube: red rounded rect + white play triangle."""
    _rounded_rect(d, cx - s, cy - s + 3, cx + s, cy + s - 3, 6, "#FF0000")
    # Play triangle (shifted slightly right, vertically centred)
    tx = cx - 7
    tri = [(tx, cy - 10), (tx, cy + 10), (tx + 16, cy)]
    d.polygon(tri, fill="#ffffff")


def draw_telegram(d: ImageDraw.ImageDraw, cx, cy, s=18):
    """Telegram: blue circle + white paper-plane."""
    d.ellipse([cx - s, cy - s, cx + s, cy + s], fill="#0088cc")
    # Paper plane: angled triangle pointing right
    plane = [
        (cx - 13, cy + 7),   # bottom-left tail
        (cx + 14, cy - 4),   # tip (right)
        (cx - 3,  cy + 13),  # bottom fold
    ]
    d.polygon(plane, fill="#ffffff")
    # Fold crease line
    d.line([(cx - 3, cy + 13), (cx + 1, cy + 4)], fill="#0088cc", width=2)
    # Small upper wing
    d.line([(cx - 13, cy + 7), (cx + 14, cy - 4)], fill="#ffffff", width=2)


# ── Layout ────────────────────────────────────────────────────────────────────

items = [
    ("instagram", "@stockswithgauravshree"),
    ("telegram",  "@stockswithgaurav"),
    ("youtube",   "StocksWithGaurav"),
]

section_w = W // 3
y_center  = H // 2
ICON_R    = 18   # icon half-size in px
GAP       = 10   # gap between icon right edge and text

_icon_fns = {
    "instagram": draw_instagram,
    "telegram":  draw_telegram,
    "youtube":   draw_youtube,
}

for i, (platform, text) in enumerate(items):
    # Place icon + text centred inside each third
    # Estimate text width to compute combined block width
    try:
        tw = font.getlength(text)
    except AttributeError:
        tw = font.getsize(text)[0]  # Pillow < 9 fallback
    block_w = ICON_R * 2 + GAP + tw
    block_x = i * section_w + (section_w - block_w) // 2

    icon_cx = block_x + ICON_R
    text_x  = block_x + ICON_R * 2 + GAP

    _icon_fns[platform](draw, icon_cx, y_center)
    draw.text((text_x, y_center - 12), text, fill="#ffffff", font=font)

img.save(str(OUT), "PNG")
print(f"Footer saved: {W}x{H} → {OUT}")
