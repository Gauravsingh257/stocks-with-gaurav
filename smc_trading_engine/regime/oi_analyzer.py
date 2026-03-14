"""
OI Analyzer Module
==================
Analyses option chain data to determine OI-based directional bias.

All functions accept explicit DataFrames / dicts for backtest compatibility.
No live API calls — data must be passed in.
"""

import logging
from typing import Dict, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ─── THRESHOLDS ────────────────────────────────────────
PCR_BULLISH_THRESHOLD = 1.3
PCR_BEARISH_THRESHOLD = 0.7


def fetch_option_chain(option_chain_df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and normalise an option-chain DataFrame.

    Expected columns:
        strike, call_oi, put_oi, call_change_oi, put_change_oi

    Args:
        option_chain_df: Raw option-chain DataFrame

    Returns:
        Cleaned DataFrame with required columns.
    """
    required = ["strike", "call_oi", "put_oi"]
    for col in required:
        if col not in option_chain_df.columns:
            raise ValueError(f"Option chain missing required column: {col}")

    df = option_chain_df.copy()

    # Ensure numeric types
    for col in ["call_oi", "put_oi"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Add change columns if missing
    if "call_change_oi" not in df.columns:
        df["call_change_oi"] = 0
    if "put_change_oi" not in df.columns:
        df["put_change_oi"] = 0

    df["call_change_oi"] = pd.to_numeric(df["call_change_oi"], errors="coerce").fillna(0).astype(int)
    df["put_change_oi"] = pd.to_numeric(df["put_change_oi"], errors="coerce").fillna(0).astype(int)

    return df.sort_values("strike").reset_index(drop=True)


def calculate_pcr(option_chain_df: pd.DataFrame) -> float:
    """
    Calculate Put-Call Ratio from total OI.

    PCR = Total Put OI / Total Call OI

    Returns:
        PCR as float. Returns 1.0 if call OI is zero (neutral fallback).
    """
    total_call_oi = option_chain_df["call_oi"].sum()
    total_put_oi = option_chain_df["put_oi"].sum()

    if total_call_oi == 0:
        return 1.0  # Neutral fallback

    return round(total_put_oi / total_call_oi, 4)


def detect_call_writing(
    option_chain_df: pd.DataFrame,
    spot_price: float,
    top_n: int = 3,
) -> Dict:
    """
    Detect heavy call writing above spot — indicates resistance.

    Looks at strikes above spot and finds the one with maximum call OI.

    Returns:
        {"call_wall": float, "call_wall_oi": int, "heavy_call_writing": bool}
    """
    above_spot = option_chain_df[option_chain_df["strike"] > spot_price].copy()

    if above_spot.empty:
        return {"call_wall": 0.0, "call_wall_oi": 0, "heavy_call_writing": False}

    # Call wall = strike with highest call OI above spot
    max_idx = above_spot["call_oi"].idxmax()
    call_wall = float(above_spot.loc[max_idx, "strike"])
    call_wall_oi = int(above_spot.loc[max_idx, "call_oi"])

    # Heavy writing = top strike OI > 2x average
    avg_call_oi = option_chain_df["call_oi"].mean()
    heavy = call_wall_oi > (2 * avg_call_oi) if avg_call_oi > 0 else False

    return {
        "call_wall": call_wall,
        "call_wall_oi": call_wall_oi,
        "heavy_call_writing": heavy,
    }


def detect_put_writing(
    option_chain_df: pd.DataFrame,
    spot_price: float,
    top_n: int = 3,
) -> Dict:
    """
    Detect heavy put writing below spot — indicates support.

    Looks at strikes below spot and finds the one with maximum put OI.

    Returns:
        {"put_wall": float, "put_wall_oi": int, "heavy_put_writing": bool}
    """
    below_spot = option_chain_df[option_chain_df["strike"] < spot_price].copy()

    if below_spot.empty:
        return {"put_wall": 0.0, "put_wall_oi": 0, "heavy_put_writing": False}

    max_idx = below_spot["put_oi"].idxmax()
    put_wall = float(below_spot.loc[max_idx, "strike"])
    put_wall_oi = int(below_spot.loc[max_idx, "put_oi"])

    avg_put_oi = option_chain_df["put_oi"].mean()
    heavy = put_wall_oi > (2 * avg_put_oi) if avg_put_oi > 0 else False

    return {
        "put_wall": put_wall,
        "put_wall_oi": put_wall_oi,
        "heavy_put_writing": heavy,
    }


def detect_max_pain(option_chain_df: pd.DataFrame) -> float:
    """
    Calculate max-pain strike — the strike at which total option buyer
    payout is minimised (i.e., option writers profit the most).

    For each strike S:
        call_pain = sum over all strikes K where K < S of: (S - K) * call_oi[K]
        put_pain  = sum over all strikes K where K > S of: (K - S) * put_oi[K]
        total_pain = call_pain + put_pain

    Max pain = strike with MINIMUM total_pain.

    Returns:
        Max pain strike as float. Returns 0.0 if data is empty.
    """
    if option_chain_df.empty:
        return 0.0

    strikes = option_chain_df["strike"].values
    call_ois = option_chain_df["call_oi"].values
    put_ois = option_chain_df["put_oi"].values

    min_pain = float("inf")
    max_pain_strike = 0.0

    for i, s in enumerate(strikes):
        # ITM calls: strikes below S → buyers get (S - K) per lot
        call_pain = sum(
            max(0, s - strikes[j]) * call_ois[j]
            for j in range(len(strikes))
        )
        # ITM puts: strikes above S → buyers get (K - S) per lot
        put_pain = sum(
            max(0, strikes[j] - s) * put_ois[j]
            for j in range(len(strikes))
        )
        total = call_pain + put_pain

        if total < min_pain:
            min_pain = total
            max_pain_strike = float(s)

    return max_pain_strike


def compute_oi_bias_score(
    option_chain_df: pd.DataFrame,
    spot_price: float,
) -> Dict:
    """
    Compute overall OI-based bias and score.

    Logic:
        PCR < 0.7 → bearish
        PCR > 1.3 → bullish
        Heavy call writing above spot → resistance (bearish tilt)
        Heavy put writing below spot → support (bullish tilt)

    Score (0-100):
        50 = neutral
        0  = maximum bearish
        100 = maximum bullish

    Returns:
        {
            "oi_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
            "oi_score": int (0-100),
            "pcr": float,
            "call_wall": float,
            "put_wall": float,
            "max_pain": float,
            "heavy_call_writing": bool,
            "heavy_put_writing": bool,
        }
    """
    chain = fetch_option_chain(option_chain_df)
    pcr = calculate_pcr(chain)
    call_data = detect_call_writing(chain, spot_price)
    put_data = detect_put_writing(chain, spot_price)
    max_pain = detect_max_pain(chain)

    # ── Score computation ──
    # PCR component (60% weight)
    # Map PCR 0.3–2.0 → score 0–100 (higher PCR = more bullish)
    pcr_clamped = max(0.3, min(2.0, pcr))
    pcr_score = ((pcr_clamped - 0.3) / (2.0 - 0.3)) * 100.0

    # Call/Put wall component (40% weight)
    wall_score = 50.0  # neutral default
    if call_data["heavy_call_writing"] and not put_data["heavy_put_writing"]:
        wall_score = 25.0  # resistance → bearish tilt
    elif put_data["heavy_put_writing"] and not call_data["heavy_call_writing"]:
        wall_score = 75.0  # support → bullish tilt
    elif call_data["heavy_call_writing"] and put_data["heavy_put_writing"]:
        wall_score = 50.0  # balanced

    oi_score = int(round(pcr_score * 0.60 + wall_score * 0.40))
    oi_score = max(0, min(100, oi_score))

    # Determine bias
    if pcr < PCR_BEARISH_THRESHOLD:
        bias = "BEARISH"
    elif pcr > PCR_BULLISH_THRESHOLD:
        bias = "BULLISH"
    else:
        bias = "NEUTRAL"

    result = {
        "oi_bias": bias,
        "oi_score": oi_score,
        "pcr": pcr,
        "call_wall": call_data["call_wall"],
        "put_wall": put_data["put_wall"],
        "max_pain": max_pain,
        "heavy_call_writing": call_data["heavy_call_writing"],
        "heavy_put_writing": put_data["heavy_put_writing"],
    }

    logger.info(
        f"[OI] bias={bias} score={oi_score} pcr={pcr:.2f} "
        f"call_wall={call_data['call_wall']} put_wall={put_data['put_wall']} "
        f"max_pain={max_pain}"
    )

    return result
