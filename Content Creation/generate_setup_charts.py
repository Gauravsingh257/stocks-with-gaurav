"""
generate_setup_charts.py — Generate annotated price-structure charts
showing exactly where each SMC setup triggers an entry.

Produces PNG images in charts/ folder:
  1. Setup-A LONG  — HTF BOS + 5m OB + FVG Tap + Rejection Entry
  2. Setup-A SHORT
  3. Setup-B LONG  — Range OB Rejection + Volume + FVG
  4. Setup-C LONG  — Multi-TF OB+FVG Zone → Tap → Reaction (UNIVERSAL)
  5. Setup-C SHORT
  6. Setup-D LONG  — CHoCH + Displacement + OB/FVG Retest
  7. Setup-D SHORT
"""

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib.lines import Line2D

OUT_DIR = os.path.join(os.path.dirname(__file__), "charts")
os.makedirs(OUT_DIR, exist_ok=True)

# ─── Color palette ───────────────────────────────────────────────
C_BG       = "#0e1117"
C_GRID     = "#1e2530"
C_BULL     = "#26a69a"   # green candle
C_BEAR     = "#ef5350"   # red candle
C_OB_LONG  = "#1b5e20"   # order block (demand)
C_OB_SHORT = "#b71c1c"   # order block (supply)
C_FVG      = "#1565c0"   # FVG zone
C_ZONE     = "#ff8f00"   # combined OB+FVG zone
C_ENTRY    = "#ffeb3b"   # entry arrow / line
C_SL       = "#f44336"   # stop loss
C_TP       = "#4caf50"   # target
C_ANNOT    = "#b0bec5"   # annotation text
C_TITLE    = "#eceff1"
C_CHOCH    = "#ce93d8"   # CHoCH line
C_BOS      = "#64b5f6"   # BOS line
C_SWEEP    = "#ff9800"   # liquidity sweep


def _style_ax(ax, title, subtitle=""):
    ax.set_facecolor(C_BG)
    ax.figure.set_facecolor(C_BG)
    ax.grid(True, color=C_GRID, linewidth=0.4, alpha=0.5)
    ax.set_title(title, color=C_TITLE, fontsize=14, fontweight="bold", pad=12)
    if subtitle:
        ax.text(0.5, 1.02, subtitle, transform=ax.transAxes,
                ha="center", va="bottom", fontsize=9, color=C_ANNOT, style="italic")
    ax.tick_params(colors=C_ANNOT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(C_GRID)
    ax.set_xlabel("")
    ax.set_ylabel("Price", color=C_ANNOT, fontsize=9)


def draw_candles(ax, candles, x_offset=0):
    """Draw OHLC candles. candles = list of (o, h, l, c)."""
    for i, (o, h, l, c) in enumerate(candles):
        x = i + x_offset
        color = C_BULL if c >= o else C_BEAR
        body_lo = min(o, c)
        body_hi = max(o, c)
        body_h = max(body_hi - body_lo, 0.15)
        # wick
        ax.plot([x, x], [l, h], color=color, linewidth=0.9, solid_capstyle="round")
        # body
        ax.add_patch(Rectangle((x - 0.35, body_lo), 0.7, body_h,
                                facecolor=color, edgecolor=color, linewidth=0.5))


def draw_zone(ax, x0, x1, y_lo, y_hi, color, label, alpha=0.22):
    """Shaded horizontal zone."""
    ax.add_patch(Rectangle((x0, y_lo), x1 - x0, y_hi - y_lo,
                            facecolor=color, edgecolor=color, alpha=alpha, linewidth=0.8))
    ax.text(x0 + 0.3, (y_lo + y_hi) / 2, label, fontsize=7.5, color=color,
            va="center", fontweight="bold", alpha=0.9)


def draw_hline(ax, y, x0, x1, color, label, ls="--", lw=1):
    ax.plot([x0, x1], [y, y], color=color, linewidth=lw, linestyle=ls)
    ax.text(x1 + 0.2, y, label, fontsize=7.5, color=color, va="center")


def draw_arrow(ax, x, y_from, y_to, color, head_width=0.3):
    ax.annotate("", xy=(x, y_to), xytext=(x, y_from),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.8))


def draw_entry_marker(ax, x, y, label="ENTRY"):
    ax.plot(x, y, marker="D", color=C_ENTRY, markersize=10, zorder=10)
    ax.annotate(label, (x, y), xytext=(x + 0.8, y),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                va="center", arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1))


def add_legend(ax, items):
    """items = [(color, label), ...]"""
    handles = [Line2D([0], [0], color=c, linewidth=6, label=l) for c, l in items]
    ax.legend(handles=handles, loc="upper left", fontsize=7,
              facecolor="#1a1a2e", edgecolor=C_GRID, labelcolor=C_ANNOT)


def save(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  ✅ Saved: {path}")


# ═══════════════════════════════════════════════════════════════════
# SETUP-C  LONG — The Core Money-Maker
# ═══════════════════════════════════════════════════════════════════
def chart_setup_c_long():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-C (UNIVERSAL) — LONG Entry Structure",
              "Multi-TF OB+FVG Zone  →  5m Tap  →  Green Close Above Zone  →  ENTRY")

    # --- Narrative price action ---
    # Phase 1: Downtrend into demand zone
    # Phase 2: Tap zone, wait
    # Phase 3: Green reaction candle closes above zone → ENTRY
    candles = [
        # Downtrend approach (candles 0-7)
        (250, 253, 247, 248),   # 0 - red
        (248, 250, 244, 245),   # 1 - red
        (245, 248, 242, 247),   # 2 - green bounce
        (247, 249, 243, 244),   # 3 - red
        (244, 246, 240, 241),   # 4 - red deeper
        (241, 244, 238, 243),   # 5 - green
        (243, 244, 237, 238),   # 6 - red, nearing zone
        (238, 239, 234, 235),   # 7 - red, into zone (TAP)
        # Zone tap & reaction (candles 8-11)
        (235, 237, 232, 233),   # 8 - red, low touches zone (TAPPED)
        (233, 236, 232, 235),   # 9 - small green inside zone
        (235, 238, 233, 234),   # 10 - doji/indecision
        (234, 242, 233, 241),   # 11 - STRONG GREEN - closes ABOVE zone high ← ENTRY!
        # Post-entry move (candles 12-16)
        (241, 246, 240, 245),   # 12 - continuation
        (245, 250, 244, 249),   # 13 - moving up
        (249, 254, 248, 253),   # 14 - target area
        (253, 256, 251, 255),   # 15 - at target
        (255, 259, 253, 257),   # 16 - overshoot
    ]
    draw_candles(ax, candles)

    # OB + FVG Combined Zone (demand)
    zone_lo, zone_hi = 230, 236
    draw_zone(ax, -0.5, 12.5, zone_lo, zone_hi, C_ZONE,
              "OB + FVG (15m/1H)", alpha=0.18)

    # Order Block portion
    draw_zone(ax, -0.5, 12.5, 230, 233, C_OB_LONG,
              "Order Block", alpha=0.12)

    # FVG portion
    draw_zone(ax, -0.5, 12.5, 233, 236, C_FVG,
              "Fair Value Gap", alpha=0.12)

    # HTF Bias arrow
    ax.annotate("HTF BIAS: BULLISH (1H)", xy=(1, 252), fontsize=9,
                color=C_BOS, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d47a1", alpha=0.6))

    # LTF Structure Bias
    ax.annotate("LTF Bias: BULLISH\n(not bearish → zone valid)", xy=(1, 249), fontsize=7.5,
                color=C_ANNOT, style="italic")

    # State annotations
    ax.annotate("ZONE: ACTIVE", xy=(3, zone_hi + 0.5), fontsize=7,
                color=C_ZONE, fontweight="bold")

    # TAP marker
    ax.annotate("TAP!\nLow ≤ Zone High", xy=(8, 232), xytext=(5.5, 227),
                fontsize=8, color=C_SWEEP, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_SWEEP, lw=1.5))
    ax.annotate("State → TAPPED", xy=(8, 229), fontsize=7, color=C_ANNOT, ha="center")

    # ENTRY candle highlight
    ax.add_patch(Rectangle((10.65, 233), 0.7, 9, facecolor=C_ENTRY, alpha=0.08))
    ax.annotate("REACTION CANDLE\n✓ GREEN (close > open)\n✓ Close ABOVE zone high",
                xy=(11, 241), xytext=(13, 244),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#33691e", alpha=0.5))

    # Entry / SL / TP lines
    entry_price = 241
    sl_price = zone_lo - 2  # z_low - buffer
    tp_price = entry_price + (entry_price - sl_price) * 2  # RR = 2.0

    draw_entry_marker(ax, 11, entry_price)
    draw_hline(ax, sl_price, 10, 17, C_SL, f"SL = Zone Low − Buffer = {sl_price}", lw=1.5)
    draw_hline(ax, tp_price, 10, 17, C_TP, f"TP = Entry + 2×Risk = {tp_price} (RR 2:1)", lw=1.5)

    # RR bracket
    ax.annotate("", xy=(16.5, entry_price), xytext=(16.5, sl_price),
                arrowprops=dict(arrowstyle="<->", color=C_SL, lw=1))
    ax.text(16.8, (entry_price + sl_price) / 2, "1R\nRisk", fontsize=7, color=C_SL, va="center")

    ax.annotate("", xy=(16.5, tp_price), xytext=(16.5, entry_price),
                arrowprops=dict(arrowstyle="<->", color=C_TP, lw=1))
    ax.text(16.8, (tp_price + entry_price) / 2, "2R\nReward", fontsize=7, color=C_TP, va="center")

    # Step labels at bottom
    steps = [
        (2, "① HTF zones\nscanned"),
        (5, "② Price\napproaches zone"),
        (8, "③ Low taps\nzone → TAPPED"),
        (11, "④ Green close\nabove zone\n→ ENTRY"),
        (14, "⑤ Price runs\nto target"),
    ]
    for x, txt in steps:
        ax.text(x, zone_lo - 8, txt, fontsize=7, color=C_ANNOT,
                ha="center", va="top",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a237e", alpha=0.4))

    add_legend(ax, [
        (C_ZONE, "OB + FVG Combined Zone"),
        (C_ENTRY, "Entry (Close of Reaction Candle)"),
        (C_SL, "Stop Loss (Zone Low − Buffer)"),
        (C_TP, "Target (2R)"),
    ])

    ax.set_xlim(-1, 18)
    ax.set_ylim(zone_lo - 12, tp_price + 8)
    ax.set_xticks([])
    save(fig, "setup_c_long.png")


# ═══════════════════════════════════════════════════════════════════
# SETUP-C  SHORT
# ═══════════════════════════════════════════════════════════════════
def chart_setup_c_short():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-C (UNIVERSAL) — SHORT Entry Structure",
              "Multi-TF OB+FVG Zone  →  5m Tap  →  Red Close Below Zone  →  ENTRY")

    candles = [
        # Uptrend approach (candles 0-7)
        (240, 243, 238, 242),   # 0
        (242, 246, 241, 245),   # 1
        (245, 247, 243, 244),   # 2
        (244, 248, 243, 247),   # 3
        (247, 250, 246, 249),   # 4
        (249, 251, 247, 248),   # 5
        (248, 253, 247, 252),   # 6
        (252, 257, 251, 256),   # 7 - into zone
        # Zone tap & reaction
        (256, 259, 255, 258),   # 8 - High taps zone (TAPPED)
        (258, 260, 256, 257),   # 9 - indecision
        (257, 258, 255, 256),   # 10 - doji
        (256, 257, 249, 250),   # 11 - STRONG RED - close BELOW zone low ← ENTRY!
        # Post-entry move
        (250, 251, 245, 246),   # 12
        (246, 247, 241, 242),   # 13
        (242, 243, 237, 238),   # 14 - target area
        (238, 240, 236, 237),   # 15
    ]
    draw_candles(ax, candles)

    # Supply zone
    zone_lo, zone_hi = 255, 261
    draw_zone(ax, -0.5, 12.5, zone_lo, zone_hi, C_OB_SHORT, "OB + FVG (Supply)", alpha=0.18)

    # HTF
    ax.annotate("HTF BIAS: BEARISH (1H)", xy=(1, 237), fontsize=9,
                color=C_SL, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#b71c1c", alpha=0.5))

    # TAP
    ax.annotate("TAP!\nHigh ≥ Zone Low", xy=(8, 259), xytext=(5, 263),
                fontsize=8, color=C_SWEEP, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_SWEEP, lw=1.5))

    # ENTRY highlight
    ax.annotate("REACTION CANDLE\n✓ RED (close < open)\n✓ Close BELOW zone low",
                xy=(11, 250), xytext=(13, 247),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#4a148c", alpha=0.5))

    entry_price = 250
    sl_price = zone_hi + 2  # z_high + buffer
    tp_price = entry_price - (sl_price - entry_price) * 2

    draw_entry_marker(ax, 11, entry_price)
    draw_hline(ax, sl_price, 10, 16, C_SL, f"SL = Zone High + Buffer = {sl_price}")
    draw_hline(ax, tp_price, 10, 16, C_TP, f"TP = Entry − 2×Risk = {tp_price} (RR 2:1)")

    add_legend(ax, [
        (C_OB_SHORT, "OB + FVG Supply Zone"),
        (C_ENTRY, "Entry (Close of Reaction Candle)"),
        (C_SL, "Stop Loss"),
        (C_TP, "Target (2R)"),
    ])

    ax.set_xlim(-1, 17)
    ax.set_ylim(tp_price - 8, zone_hi + 10)
    ax.set_xticks([])
    save(fig, "setup_c_short.png")


# ═══════════════════════════════════════════════════════════════════
# SETUP-A  LONG — BOS + OB + FVG Tap + Rejection
# ═══════════════════════════════════════════════════════════════════
def chart_setup_a_long():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-A — LONG Entry Structure",
              "1H Bullish BOS  →  5m OB + FVG Formed  →  FVG Tap  →  Rejection Candle  →  ENTRY")

    candles = [
        # 1H bullish trend context (implied) — show 5m structure
        # BOS swing: HH + HL sequence
        (230, 234, 229, 233),   # 0 - impulse up
        (233, 238, 232, 237),   # 1 - strong move up (BOS candle)
        (237, 240, 236, 239),   # 2 - continuation
        (239, 241, 237, 238),   # 3 - small pullback
        (238, 240, 235, 236),   # 4 - pullback (OB forms here)
        (236, 237, 233, 234),   # 5 - deeper pullback (FVG area)
        # FVG gap between candle 3 low (237) and candle 5 high (237) — 
        # Let's adjust to make FVG visible
        (234, 236, 232, 233),   # 6 - pullback continues, creates FVG
        (233, 234, 230, 231),   # 7 - FVG tapped! Price enters the gap
        (231, 233, 230, 232),   # 8 - inside FVG, waiting for rejection
        (232, 239, 231, 238),   # 9 - REJECTION CANDLE! Strong green ← ENTRY
        # Post-entry
        (238, 242, 237, 241),   # 10
        (241, 245, 240, 244),   # 11
        (244, 248, 243, 247),   # 12 - target
        (247, 250, 246, 249),   # 13
    ]
    draw_candles(ax, candles)

    # HTF BOS marker
    ax.annotate("", xy=(1.5, 238), xytext=(0, 230),
                arrowprops=dict(arrowstyle="-|>", color=C_BOS, lw=2.5, linestyle="-"))
    ax.text(0.3, 234, "BOS ↑\n(1H Bullish)", fontsize=8, color=C_BOS, fontweight="bold",
            rotation=55)

    # HH / HL labels
    ax.annotate("HH", xy=(2, 240), fontsize=7, color=C_BOS, fontweight="bold", ha="center")
    ax.annotate("HL", xy=(6, 232), fontsize=7, color=C_BOS, fontweight="bold", ha="center")

    # Order Block
    ob_lo, ob_hi = 234, 237
    draw_zone(ax, 3.5, 10, ob_lo, ob_hi, C_OB_LONG, "Order Block (5m)", alpha=0.15)

    # FVG
    fvg_lo, fvg_hi = 231, 234
    draw_zone(ax, 5.5, 10, fvg_lo, fvg_hi, C_FVG, "FVG (5m)", alpha=0.18)

    # Stage annotations
    ax.annotate("① FORMED\nOB + FVG detected", xy=(5, 228), fontsize=7, color=C_ANNOT,
                ha="center", bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a237e", alpha=0.4))

    ax.annotate("② FVG TAPPED\nPrice enters FVG", xy=(7.5, 228), fontsize=7, color=C_ANNOT,
                ha="center", bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a237e", alpha=0.4))

    # Rejection candle highlight
    ax.add_patch(Rectangle((8.65, 231), 0.7, 8, facecolor=C_ENTRY, alpha=0.08))
    ax.annotate("③ REJECTION!\nStrong green candle\nfrom FVG → ENTRY",
                xy=(9, 238), xytext=(11, 241),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#33691e", alpha=0.5))

    # Entry / SL / TP
    entry_price = 235  # midpoint of FVG
    sl_price = 228  # recent low - buffer
    tp_price = entry_price + 2 * (entry_price - sl_price)

    draw_entry_marker(ax, 9, entry_price, "ENTRY\n(FVG mid)")
    draw_hline(ax, sl_price, 8, 14, C_SL, f"SL = Recent Low − Buffer = {sl_price}")
    draw_hline(ax, tp_price, 8, 14, C_TP, f"TP = Entry + 2R = {tp_price}")

    add_legend(ax, [
        (C_BOS, "Break of Structure (1H Bullish)"),
        (C_OB_LONG, "Order Block (5m)"),
        (C_FVG, "Fair Value Gap (5m)"),
        (C_ENTRY, "Entry at FVG Rejection"),
        (C_SL, "Stop Loss"),
        (C_TP, "Target (2R)"),
    ])

    ax.set_xlim(-1, 15)
    ax.set_ylim(sl_price - 5, tp_price + 5)
    ax.set_xticks([])
    save(fig, "setup_a_long.png")


# ═══════════════════════════════════════════════════════════════════
# SETUP-A  SHORT
# ═══════════════════════════════════════════════════════════════════
def chart_setup_a_short():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-A — SHORT Entry Structure",
              "1H Bearish BOS  →  5m OB + FVG Formed  →  FVG Tap  →  Rejection Candle  →  ENTRY")

    candles = [
        (260, 262, 256, 257),   # 0 - impulse down
        (257, 258, 251, 252),   # 1 - BOS candle (strong down)
        (252, 254, 250, 251),   # 2 - continuation
        (251, 254, 250, 253),   # 3 - small pullback up
        (253, 257, 252, 256),   # 4 - pullback into OB area
        (256, 259, 255, 258),   # 5 - deeper pullback (FVG area)
        (258, 261, 257, 260),   # 6 - into FVG
        (260, 262, 259, 261),   # 7 - FVG tapped
        (261, 262, 259, 260),   # 8 - indecision at FVG
        (260, 261, 253, 254),   # 9 - REJECTION! Strong red ← ENTRY
        # Post-entry
        (254, 255, 249, 250),   # 10
        (250, 251, 245, 246),   # 11 - target
        (246, 248, 244, 245),   # 12
    ]
    draw_candles(ax, candles)

    # BOS marker
    ax.annotate("", xy=(1.5, 252), xytext=(0, 261),
                arrowprops=dict(arrowstyle="-|>", color=C_SL, lw=2.5))
    ax.text(0.3, 257, "BOS ↓\n(1H Bearish)", fontsize=8, color=C_SL, fontweight="bold",
            rotation=-55)

    # OB (supply)
    draw_zone(ax, 3.5, 10, 255, 258, C_OB_SHORT, "Order Block (5m)", alpha=0.15)

    # FVG
    draw_zone(ax, 5.5, 10, 258, 262, C_FVG, "FVG (5m)", alpha=0.18)

    # Rejection
    ax.annotate("REJECTION!\nStrong red from FVG",
                xy=(9, 254), xytext=(11, 252),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#4a148c", alpha=0.5))

    entry_price = 257  # FVG mid
    sl_price = 264     # recent high + buffer
    tp_price = entry_price - 2 * (sl_price - entry_price)

    draw_entry_marker(ax, 9, entry_price, "ENTRY\n(FVG mid)")
    draw_hline(ax, sl_price, 8, 13, C_SL, f"SL = Recent High + Buffer = {sl_price}")
    draw_hline(ax, tp_price, 8, 13, C_TP, f"TP = Entry − 2R = {tp_price}")

    add_legend(ax, [
        (C_SL, "Break of Structure (1H Bearish)"),
        (C_OB_SHORT, "Order Block (Supply)"),
        (C_FVG, "Fair Value Gap"),
        (C_ENTRY, "Entry at FVG Rejection"),
    ])

    ax.set_xlim(-1, 14)
    ax.set_ylim(tp_price - 5, sl_price + 5)
    ax.set_xticks([])
    save(fig, "setup_a_short.png")


# ═══════════════════════════════════════════════════════════════════
# SETUP-B  LONG — Range Mean-Reversion
# ═══════════════════════════════════════════════════════════════════
def chart_setup_b_long():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-B — LONG Mean-Reversion Entry",
              "Range Market  →  OB at Range Low + FVG  →  Tap + Volume Spike + Green Candle  →  ENTRY")

    candles = [
        # Range-bound market
        (250, 254, 249, 253),   # 0
        (253, 256, 252, 255),   # 1
        (255, 258, 254, 256),   # 2 - range high
        (256, 257, 253, 254),   # 3
        (254, 255, 250, 251),   # 4 - back to mid
        (251, 254, 250, 253),   # 5
        (253, 256, 252, 255),   # 6 - range high test
        (255, 257, 253, 254),   # 7 - rejection at top
        (254, 255, 250, 251),   # 8
        (251, 252, 247, 248),   # 9 - moving to range low
        (248, 249, 244, 245),   # 10 - near OB at range low
        (245, 246, 242, 243),   # 11 - taps OB (TAP)
        (243, 244, 241, 242),   # 12 - still in OB area
        (242, 249, 241, 248),   # 13 - GREEN + VOLUME SPIKE ← ENTRY!
        # Post-entry
        (248, 252, 247, 251),   # 14
        (251, 255, 250, 254),   # 15
        (254, 258, 253, 257),   # 16 - target (range high)
    ]
    draw_candles(ax, candles)

    # Range boundaries
    range_hi = 258
    range_lo = 241
    draw_hline(ax, range_hi, -0.5, 17, "#78909c", "Range High", ls="-.", lw=0.8)
    draw_hline(ax, range_lo, -0.5, 17, "#78909c", "Range Low", ls="-.", lw=0.8)
    ax.text(8, range_hi + 1.5, "RANGE-BOUND MARKET", fontsize=9, color="#78909c",
            ha="center", fontweight="bold")

    # OB at range low
    ob_lo, ob_hi = 241, 245
    draw_zone(ax, 9, 14, ob_lo, ob_hi, C_OB_LONG, "Order Block\n(Range Low)", alpha=0.18)

    # FVG
    fvg_lo, fvg_hi = 243, 246
    draw_zone(ax, 10, 14, fvg_lo, fvg_hi, C_FVG, "FVG", alpha=0.15)

    # Volume spike annotation
    ax.annotate("📊 VOLUME SPIKE!\nConfirmation candle",
                xy=(13, 248), xytext=(15, 251),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#33691e", alpha=0.5))

    entry_price = 248
    sl_price = ob_lo - 2  # z_low - ATR*0.3
    tp_price = range_hi  # mean reversion to range top

    draw_entry_marker(ax, 13, entry_price)
    draw_hline(ax, sl_price, 12, 17, C_SL, f"SL = OB Low − Buffer = {sl_price}")
    draw_hline(ax, tp_price, 12, 17, C_TP, f"TP = Range High = {tp_price}")

    # Requirements box
    ax.text(0.5, 236, "REQUIREMENTS:\n✓ OB at range extreme\n✓ FVG overlapping OB\n"
            "✓ Last 3 candles tap zone\n✓ Current candle GREEN\n✓ Volume expansion",
            fontsize=7.5, color=C_ANNOT,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#1a237e", alpha=0.5))

    add_legend(ax, [
        ("#78909c", "Range Boundaries"),
        (C_OB_LONG, "Order Block (Demand)"),
        (C_FVG, "Fair Value Gap"),
        (C_ENTRY, "Entry (Green + Volume)"),
        (C_SL, "Stop Loss"),
        (C_TP, "Target (Range High)"),
    ])

    ax.set_xlim(-1, 18)
    ax.set_ylim(sl_price - 5, range_hi + 8)
    ax.set_xticks([])
    save(fig, "setup_b_long.png")


# ═══════════════════════════════════════════════════════════════════
# SETUP-D  LONG — CHoCH + Displacement + OB/FVG Retest
# ═══════════════════════════════════════════════════════════════════
def chart_setup_d_long():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-D — LONG Entry Structure",
              "CHoCH (Trend Reversal)  →  Displacement Candle  →  OB+FVG Retest  →  Close Above OB  →  ENTRY")

    candles = [
        # Prior downtrend
        (260, 262, 256, 257),   # 0 - down
        (257, 258, 253, 254),   # 1 - down
        (254, 255, 250, 251),   # 2 - down — swing low
        (251, 252, 248, 249),   # 3 - final push down (low of move)
        # CHoCH — breaks above previous lower high
        (249, 257, 248, 256),   # 4 - DISPLACEMENT CANDLE (body > 1.2× avg range)
        (256, 260, 255, 259),   # 5 - continuation (CHoCH confirmed)
        (259, 261, 257, 258),   # 6 - small pullback
        # Retest phase — OB and FVG below
        (258, 259, 254, 255),   # 7 - pullback into OB area
        (255, 256, 252, 253),   # 8 - taps FVG (TAPPED)
        (253, 254, 251, 252),   # 9 - still at FVG
        (252, 259, 251, 258),   # 10 - CLOSE > OB HIGH ← ENTRY!
        # Post-entry
        (258, 263, 257, 262),   # 11
        (262, 266, 261, 265),   # 12
        (265, 269, 264, 268),   # 13 - target
    ]
    draw_candles(ax, candles)

    # Downtrend lines
    ax.plot([0, 3], [260, 249], color=C_BEAR, linewidth=1.5, linestyle=":", alpha=0.6)

    # Previous lower high level (CHoCH break level)
    choch_level = 255
    ax.plot([0, 6], [choch_level, choch_level], color=C_CHOCH, linewidth=1, linestyle="--", alpha=0.7)
    ax.annotate("Previous Lower High", xy=(0, choch_level + 0.5), fontsize=7,
                color=C_CHOCH, alpha=0.8)

    # CHoCH marker
    ax.annotate("CHoCH!\nBreaks above\nprevious LH",
                xy=(4, 256), xytext=(1, 263),
                fontsize=8, color=C_CHOCH, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_CHOCH, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#4a148c", alpha=0.5))

    # Displacement candle highlight
    ax.add_patch(Rectangle((3.65, 248), 0.7, 9, facecolor=C_CHOCH, alpha=0.08))
    ax.annotate("DISPLACEMENT\nBody > 1.2× avg range",
                xy=(4, 249), xytext=(5.5, 245),
                fontsize=7, color=C_CHOCH,
                arrowprops=dict(arrowstyle="->", color=C_CHOCH, lw=1))

    # OB zone
    ob_lo, ob_hi = 253, 257
    draw_zone(ax, 3.5, 11, ob_lo, ob_hi, C_OB_LONG, "Order Block", alpha=0.15)

    # FVG
    fvg_lo, fvg_hi = 250, 254
    draw_zone(ax, 3.5, 11, fvg_lo, fvg_hi, C_FVG, "FVG", alpha=0.15)

    # FVG Tap
    ax.annotate("FVG TAPPED", xy=(8, 252), xytext=(6, 246),
                fontsize=7, color=C_FVG, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_FVG, lw=1))

    # HTF alignment
    ax.annotate("1H BIAS: BULLISH ✓\n(Must align with CHoCH direction)",
                xy=(8, 268), fontsize=8, color=C_BOS, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d47a1", alpha=0.5))

    # Entry trigger
    ax.annotate("CLOSE > OB HIGH ✓\n→ ENTRY!",
                xy=(10, 258), xytext=(12, 261),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#33691e", alpha=0.5))

    entry_price = (fvg_lo + fvg_hi) / 2  # FVG midpoint = 252
    sl_price = ob_lo - 3  # OB low - buffer
    tp_price = entry_price + 2 * (entry_price - sl_price)

    draw_entry_marker(ax, 10, entry_price, "ENTRY\n(FVG mid)")
    draw_hline(ax, sl_price, 9, 14, C_SL, f"SL = OB Low − Buffer = {sl_price}")
    draw_hline(ax, tp_price, 9, 14, C_TP, f"TP = 2R = {tp_price}")

    # Phase labels
    ax.text(1.5, 245, "Phase 1:\nSTRUCTURE\nCHoCH + Displ\n+ OB + FVG", fontsize=6.5,
            color=C_ANNOT, ha="center",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a237e", alpha=0.4))
    ax.text(9, 245, "Phase 2:\nEXECUTION\nFVG Tap →\nClose > OB", fontsize=6.5,
            color=C_ANNOT, ha="center",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a237e", alpha=0.4))

    add_legend(ax, [
        (C_CHOCH, "CHoCH + Displacement"),
        (C_OB_LONG, "Order Block"),
        (C_FVG, "Fair Value Gap"),
        (C_ENTRY, "Entry (FVG Midpoint)"),
        (C_SL, "Stop Loss"),
        (C_TP, "Target (2R)"),
    ])

    ax.set_xlim(-1, 15)
    ax.set_ylim(sl_price - 5, tp_price + 5)
    ax.set_xticks([])
    save(fig, "setup_d_long.png")


# ═══════════════════════════════════════════════════════════════════
# SETUP-D  SHORT
# ═══════════════════════════════════════════════════════════════════
def chart_setup_d_short():
    fig, ax = plt.subplots(figsize=(14, 7.5))
    _style_ax(ax, "SETUP-D — SHORT Entry Structure",
              "CHoCH (Bearish Reversal)  →  Displacement  →  OB+FVG Retest  →  Close Below OB  →  ENTRY")

    candles = [
        # Prior uptrend
        (240, 244, 239, 243),   # 0
        (243, 247, 242, 246),   # 1
        (246, 250, 245, 249),   # 2
        (249, 253, 248, 252),   # 3 - swing high
        # CHoCH — breaks below previous higher low
        (252, 253, 244, 245),   # 4 - DISPLACEMENT DOWN
        (245, 246, 241, 242),   # 5 - continuation (CHoCH confirmed)
        (242, 244, 241, 243),   # 6 - small bounce
        # Retest
        (243, 247, 242, 246),   # 7 - pullback into OB
        (246, 249, 245, 248),   # 8 - taps FVG (TAPPED)
        (248, 249, 247, 248),   # 9 - test OB
        (248, 249, 241, 242),   # 10 - CLOSE < OB LOW ← ENTRY!
        # Post-entry
        (242, 243, 237, 238),   # 11
        (238, 239, 233, 234),   # 12
        (234, 235, 230, 231),   # 13 - target
    ]
    draw_candles(ax, candles)

    # CHoCH
    choch_level = 246
    ax.plot([0, 6], [choch_level, choch_level], color=C_CHOCH, linewidth=1, linestyle="--", alpha=0.7)
    ax.annotate("CHoCH!\nBreaks below\nprevious HL",
                xy=(4, 245), xytext=(1, 237),
                fontsize=8, color=C_CHOCH, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_CHOCH, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#4a148c", alpha=0.5))

    # OB (supply)
    ob_lo, ob_hi = 244, 248
    draw_zone(ax, 3.5, 11, ob_lo, ob_hi, C_OB_SHORT, "Order Block", alpha=0.15)

    # FVG
    fvg_lo, fvg_hi = 247, 251
    draw_zone(ax, 3.5, 11, fvg_lo, fvg_hi, C_FVG, "FVG", alpha=0.15)

    # Entry
    ax.annotate("CLOSE < OB LOW ✓\n→ ENTRY!",
                xy=(10, 242), xytext=(12, 240),
                fontsize=8, color=C_ENTRY, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_ENTRY, lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#4a148c", alpha=0.5))

    entry_price = (fvg_lo + fvg_hi) / 2  # 249
    sl_price = ob_hi + 3  # OB high + buffer
    tp_price = entry_price - 2 * (sl_price - entry_price)

    draw_entry_marker(ax, 10, entry_price, "ENTRY\n(FVG mid)")
    draw_hline(ax, sl_price, 9, 14, C_SL, f"SL = OB High + Buffer = {sl_price}")
    draw_hline(ax, tp_price, 9, 14, C_TP, f"TP = 2R = {tp_price}")

    add_legend(ax, [
        (C_CHOCH, "CHoCH + Displacement"),
        (C_OB_SHORT, "Order Block (Supply)"),
        (C_FVG, "Fair Value Gap"),
        (C_ENTRY, "Entry (FVG Midpoint)"),
        (C_SL, "Stop Loss"),
        (C_TP, "Target (2R)"),
    ])

    ax.set_xlim(-1, 15)
    ax.set_ylim(tp_price - 5, sl_price + 8)
    ax.set_xticks([])
    save(fig, "setup_d_short.png")


# ═══════════════════════════════════════════════════════════════════
# OVERVIEW — All Setups Side by Side
# ═══════════════════════════════════════════════════════════════════
def chart_overview():
    fig, axes = plt.subplots(2, 2, figsize=(18, 14))
    fig.suptitle("SMC TRADING ENGINE — All Entry Structures Overview",
                 color=C_TITLE, fontsize=16, fontweight="bold", y=0.98)
    fig.set_facecolor(C_BG)

    # ── Setup-A (top-left) ──
    ax = axes[0, 0]
    _style_ax(ax, "SETUP-A: BOS → OB+FVG → Tap → Rejection")
    candles_a = [
        (230, 234, 229, 233), (233, 238, 232, 237), (237, 240, 236, 239),
        (239, 240, 235, 236), (236, 237, 233, 234), (234, 236, 232, 233),
        (233, 234, 230, 231), (231, 238, 230, 237),
        (237, 241, 236, 240), (240, 244, 239, 243),
    ]
    draw_candles(ax, candles_a)
    draw_zone(ax, 3.5, 8, 233, 236, C_OB_LONG, "OB", alpha=0.2)
    draw_zone(ax, 5, 8, 230, 233, C_FVG, "FVG", alpha=0.2)
    draw_entry_marker(ax, 7, 235)
    ax.annotate("BOS ↑", xy=(1, 238), fontsize=8, color=C_BOS, fontweight="bold")
    ax.annotate("Rejection\nfrom FVG", xy=(7, 237), xytext=(8.5, 240),
                fontsize=7, color=C_ENTRY, arrowprops=dict(arrowstyle="->", color=C_ENTRY))
    ax.set_xlim(-0.5, 10.5)
    ax.set_ylim(226, 247)
    ax.set_xticks([])

    # ── Setup-B (top-right) ──
    ax = axes[0, 1]
    _style_ax(ax, "SETUP-B: Range → OB at Extreme → Volume Reversal")
    candles_b = [
        (252, 256, 251, 255), (255, 258, 254, 256), (256, 257, 253, 254),
        (254, 255, 250, 251), (251, 253, 247, 248), (248, 249, 244, 245),
        (245, 246, 242, 243), (243, 249, 242, 248),
        (248, 253, 247, 252), (252, 257, 251, 256),
    ]
    draw_candles(ax, candles_b)
    draw_hline(ax, 258, -0.5, 10, "#78909c", "Range High", ls="-.", lw=0.7)
    draw_hline(ax, 241, -0.5, 10, "#78909c", "Range Low", ls="-.", lw=0.7)
    draw_zone(ax, 4, 8, 242, 246, C_OB_LONG, "OB", alpha=0.2)
    draw_entry_marker(ax, 7, 248)
    ax.annotate("Volume\nSpike!", xy=(7, 249), xytext=(8.5, 253),
                fontsize=7, color=C_ENTRY, arrowprops=dict(arrowstyle="->", color=C_ENTRY))
    ax.set_xlim(-0.5, 10.5)
    ax.set_ylim(237, 262)
    ax.set_xticks([])

    # ── Setup-C (bottom-left) ──
    ax = axes[1, 0]
    _style_ax(ax, "SETUP-C: Multi-TF Zone → Tap → Reaction ★")
    candles_c = [
        (252, 254, 249, 250), (250, 251, 246, 247), (247, 249, 244, 245),
        (245, 246, 241, 242), (242, 243, 238, 239), (239, 240, 236, 237),
        (237, 238, 234, 235), (235, 238, 233, 234),
        (234, 242, 233, 241),  # reaction
        (241, 246, 240, 245), (245, 250, 244, 249),
    ]
    draw_candles(ax, candles_c)
    draw_zone(ax, -0.5, 9, 232, 237, C_ZONE, "OB+FVG\n(15m/1H)", alpha=0.2)
    draw_entry_marker(ax, 8, 241)
    ax.annotate("Tap\nzone", xy=(6, 234), fontsize=7, color=C_SWEEP, fontweight="bold")
    ax.annotate("Green close\nabove zone!", xy=(8, 241), xytext=(9.5, 244),
                fontsize=7, color=C_ENTRY, arrowprops=dict(arrowstyle="->", color=C_ENTRY))
    ax.text(5, 229, "★ BEST PERFORMER — WR 52%, E +0.18R", fontsize=7,
            color=C_ENTRY, ha="center", fontweight="bold")
    ax.set_xlim(-0.5, 11.5)
    ax.set_ylim(227, 253)
    ax.set_xticks([])

    # ── Setup-D (bottom-right) ──
    ax = axes[1, 1]
    _style_ax(ax, "SETUP-D: CHoCH → Displacement → OB/FVG Retest")
    candles_d = [
        (256, 258, 252, 253), (253, 254, 249, 250), (250, 251, 247, 248),
        (248, 256, 247, 255),  # displacement
        (255, 258, 254, 257), (257, 258, 254, 255),
        (255, 257, 253, 254), (254, 255, 251, 252),
        (252, 258, 251, 257),  # reaction
        (257, 261, 256, 260), (260, 264, 259, 263),
    ]
    draw_candles(ax, candles_d)
    draw_zone(ax, 2.5, 9, 252, 256, C_OB_LONG, "OB", alpha=0.15)
    draw_zone(ax, 2.5, 9, 249, 253, C_FVG, "FVG", alpha=0.15)
    draw_entry_marker(ax, 8, 252)
    ax.annotate("CHoCH", xy=(3, 255), fontsize=8, color=C_CHOCH, fontweight="bold")
    ax.annotate("Displ.", xy=(3, 248), fontsize=7, color=C_CHOCH)
    ax.annotate("Retest\n+ Close > OB", xy=(8, 257), xytext=(9.5, 260),
                fontsize=7, color=C_ENTRY, arrowprops=dict(arrowstyle="->", color=C_ENTRY))
    ax.set_xlim(-0.5, 11.5)
    ax.set_ylim(244, 267)
    ax.set_xticks([])

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    save(fig, "all_setups_overview.png")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Generating SMC setup entry structure charts...\n")
    chart_setup_c_long()
    chart_setup_c_short()
    chart_setup_a_long()
    chart_setup_a_short()
    chart_setup_b_long()
    chart_setup_d_long()
    chart_setup_d_short()
    chart_overview()
    print(f"\n✅ All charts saved in: {OUT_DIR}")
