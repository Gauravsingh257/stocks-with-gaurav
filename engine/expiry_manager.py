"""
engine/expiry_manager.py — Centralized Expiry & Strike Management
==================================================================
Handles ATM±1 strike selection, expiry detection, and smart rollover
with preloading for NIFTY (weekly+monthly) and BANKNIFTY (monthly).

Key Responsibilities:
─────────────────────
  1. ATM±1 strike computation (4 contracts per underlying per expiry)
  2. Weekly/monthly expiry detection from instruments list
  3. Next-expiry preloading when ≤2 trading days before rollover
  4. Per-contract state isolation during transitions

Contract Budget (Steady State):
────────────────────────────────
  BANKNIFTY: 2CE + 2PE × 1 monthly                     =  4
  NIFTY:     2CE + 2PE × 2 (weekly + monthly)           =  8
  Total                                                  = 12
  During preload: temporarily up to ~16, drops after expiry.

ATM±1 Strike Rules:
────────────────────
  CE: [ATM - step, ATM]   → one below ATM, ATM itself
  PE: [ATM, ATM + step]   → ATM itself, one above ATM

  Example (BANKNIFTY spot=61187, step=100):
    ATM = 61200
    CE: [61100, 61200]
    PE: [61200, 61300]
    Total = 4 contracts (monthly only)

  Example (NIFTY CMP=25496, step=100):
    ATM = 25500
    CE: [25400, 25500]
    PE: [25500, 25600]
    Total = 8 contracts (weekly + monthly)

Expiry Rules:
─────────────
  BANKNIFTY: Monthly only
  NIFTY:     Weekly + Monthly

  Preload Rules:
    ≤ EXPIRY_PRELOAD_DAYS before expiry → begin tracking next expiry
    On expiry day after 15:30 → drop expired contracts
    Reset only per-contract state, NEVER global engine state
"""

import logging
from datetime import datetime, date as dt_date, timedelta, time

from engine import config as cfg

logger = logging.getLogger(__name__)

# Configuration
EXPIRY_PRELOAD_DAYS = getattr(cfg, "EXPIRY_PRELOAD_DAYS", 3)
EXPIRY_ATM_DRIFT_CHECK_SECS = getattr(cfg, "EXPIRY_ATM_DRIFT_CHECK_SECS", 120)


# =====================================================
# ATM STRIKE COMPUTATION
# =====================================================

def get_atm_strikes(spot, step):
    """
    Compute ATM±1 strikes for CE and PE options.

    CE: [ATM - step, ATM]   (one below ATM, ATM)
    PE: [ATM, ATM + step]   (ATM, one above ATM)

    Uses standard rounding (0.5 always rounds UP, not Python banker's rounding).

    Args:
        spot: Current underlying price (float)
        step: Strike step (int, e.g. 100 for NIFTY and BANKNIFTY)

    Returns:
        tuple: (ce_strikes: list[int], pe_strikes: list[int])

    Examples:
        >>> get_atm_strikes(61187, 100)
        ([61100, 61200], [61200, 61300])
        >>> get_atm_strikes(25496, 100)
        ([25400, 25500], [25500, 25600])
        >>> get_atm_strikes(25500, 100)   # exact ATM
        ([25400, 25500], [25500, 25600])
    """
    if step <= 0:
        raise ValueError(f"Step must be positive, got {step}")
    if spot <= 0:
        raise ValueError(f"Spot must be positive, got {spot}")

    # Standard rounding: 0.5 rounds UP (not banker's rounding)
    atm = int(spot / step + 0.5) * step

    ce_strikes = [atm - step, atm]
    pe_strikes = [atm, atm + step]

    return ce_strikes, pe_strikes


def get_atm(spot, step):
    """Return single ATM strike using standard rounding."""
    if step <= 0:
        return 0
    return int(spot / step + 0.5) * step


# =====================================================
# EXPIRY DETECTION
# =====================================================

def _collect_expiries(instruments, index_name):
    """
    Collect all future (>= today) expiry dates for an index from instruments list.

    Args:
        instruments: list of instrument dicts from Kite API
        index_name: e.g. "NIFTY", "BANKNIFTY"

    Returns:
        list of date objects, sorted ascending
    """
    today = dt_date.today()
    expiries = set()
    for i in instruments:
        if i.get("name") != index_name:
            continue
        if i.get("instrument_type") not in ("CE", "PE"):
            continue
        exp = i.get("expiry")
        if exp is None:
            continue
        if isinstance(exp, datetime):
            exp = exp.date()
        if exp >= today:
            expiries.add(exp)
    return sorted(expiries)


def get_active_weekly_expiry(instruments, index_name):
    """
    Get the nearest (current active) weekly expiry.

    Returns:
        date or None
    """
    expiries = _collect_expiries(instruments, index_name)
    return expiries[0] if expiries else None


def get_next_weekly_expiry(instruments, index_name):
    """
    Get the SECOND nearest weekly expiry (next one after current).

    Returns:
        date or None
    """
    expiries = _collect_expiries(instruments, index_name)
    return expiries[1] if len(expiries) >= 2 else None


def get_active_monthly_expiry(instruments, index_name):
    """
    Get the last expiry of the current expiry month.
    Monthly expiry = last expiry date in the same calendar month as
    the nearest expiry.

    Returns:
        date or None
    """
    expiries = _collect_expiries(instruments, index_name)
    if not expiries:
        return None

    # The nearest expiry defines the current "cycle month"
    nearest = expiries[0]
    month_expiries = [e for e in expiries
                      if e.year == nearest.year and e.month == nearest.month]
    return max(month_expiries) if month_expiries else None


def get_next_monthly_expiry(instruments, index_name):
    """
    Get the last expiry in the month AFTER the current monthly expiry month.

    Returns:
        date or None
    """
    current_monthly = get_active_monthly_expiry(instruments, index_name)
    if not current_monthly:
        return None

    expiries = _collect_expiries(instruments, index_name)

    # Find expiries after the current monthly
    next_month_expiries = [e for e in expiries if e > current_monthly]
    if not next_month_expiries:
        return None

    # Group by month and take the last expiry of the first future month
    first_future = next_month_expiries[0]
    same_month = [e for e in next_month_expiries
                  if e.year == first_future.year and e.month == first_future.month]
    return max(same_month) if same_month else None


# =====================================================
# NEAR-EXPIRY DETECTION
# =====================================================

def is_near_expiry(expiry_date, days=None):
    """
    Check if expiry_date is within `days` calendar days from today.

    Uses calendar days as a proxy for trading days:
      3 calendar days ≈ 2 trading days (handles most weekday cases).
      For holidays, the slightly larger window provides a safety margin.

    Args:
        expiry_date: date object
        days: number of calendar days threshold (default: EXPIRY_PRELOAD_DAYS)

    Returns:
        True if expiry is within the threshold
    """
    threshold = days if days is not None else EXPIRY_PRELOAD_DAYS
    if isinstance(expiry_date, datetime):
        expiry_date = expiry_date.date()

    today = dt_date.today()
    delta = (expiry_date - today).days

    return 0 <= delta <= threshold


def is_expiry_today(expiry_date):
    """Check if today is the expiry day."""
    if isinstance(expiry_date, datetime):
        expiry_date = expiry_date.date()
    return expiry_date == dt_date.today()


def is_post_expiry_cutoff():
    """Check if current time is after 3:30 PM (post-expiry cleanup cutoff)."""
    return datetime.now().time() >= time(15, 30)


# =====================================================
# TARGET EXPIRY BUILDER
# =====================================================

def get_target_expiries(instruments, index_name):
    """
    Determine which expiries should be actively monitored for an underlying.

    Rules:
        BANKNIFTY:
          → Monthly only
          → Preload next monthly when ≤ EXPIRY_PRELOAD_DAYS away

        NIFTY:
          → Weekly + Monthly
          → Preload next weekly/monthly when ≤ EXPIRY_PRELOAD_DAYS away

    Returns:
        list of dicts: [{"expiry": date, "type": "weekly"|"monthly", "preload": bool}]
    """
    result = []

    weekly = get_active_weekly_expiry(instruments, index_name)
    monthly = get_active_monthly_expiry(instruments, index_name)

    if index_name == "BANKNIFTY":
        # Monthly only
        if monthly:
            result.append({"expiry": monthly, "type": "monthly", "preload": False})

            # Preload next monthly if near expiry
            if is_near_expiry(monthly):
                next_monthly = get_next_monthly_expiry(instruments, index_name)
                if next_monthly and next_monthly != monthly:
                    result.append({"expiry": next_monthly, "type": "monthly", "preload": True})

    elif index_name == "NIFTY":
        # Weekly + Monthly
        if weekly:
            result.append({"expiry": weekly, "type": "weekly", "preload": False})

            # Preload next weekly if near expiry
            if is_near_expiry(weekly):
                next_weekly = get_next_weekly_expiry(instruments, index_name)
                if next_weekly and next_weekly != weekly:
                    # Don't duplicate if next weekly == current monthly
                    if not monthly or next_weekly != monthly:
                        result.append({"expiry": next_weekly, "type": "weekly", "preload": True})

        if monthly and monthly != weekly:
            result.append({"expiry": monthly, "type": "monthly", "preload": False})

            # Preload next monthly if near expiry
            if is_near_expiry(monthly):
                next_monthly = get_next_monthly_expiry(instruments, index_name)
                if next_monthly and next_monthly != monthly:
                    # Don't duplicate
                    existing = set(r["expiry"] for r in result)
                    if next_monthly not in existing:
                        result.append({"expiry": next_monthly, "type": "monthly", "preload": True})

    else:
        # Generic fallback: use nearest expiry
        if weekly:
            result.append({"expiry": weekly, "type": "weekly", "preload": False})

    # Deduplicate by expiry date (keep first occurrence)
    seen = set()
    deduped = []
    for r in result:
        if r["expiry"] not in seen:
            seen.add(r["expiry"])
            deduped.append(r)

    return deduped


# =====================================================
# ROLLOVER STATE MACHINE
# =====================================================

class ExpiryRolloverState:
    """
    Tracks expiry rollover state per underlying.

    Responsibilities:
      - Detecting new expiry additions (for preloading)
      - Detecting expiry drops (for cleanup)
      - Providing info about which contracts to clean up

    IMPORTANT: NEVER touches global engine state (circuit breaker,
      risk management, daily PnL). Only manages per-contract state.
    """

    def __init__(self):
        # {underlying: set(date)} — currently active expiry dates
        self.active_expiries = {}

        # {underlying: set(date)} — expiries that have been rolled off
        self.expired = {}

        # Last check timestamp
        self.last_check = None

    def update(self, underlying, current_expiries):
        """
        Update active expiries for an underlying.

        Args:
            underlying: e.g. "BANKNIFTY"
            current_expiries: set or list of date objects currently being tracked

        Returns:
            dict: {"added": list[date], "expired": list[date]}
        """
        if underlying not in self.active_expiries:
            self.active_expiries[underlying] = set()
        if underlying not in self.expired:
            self.expired[underlying] = set()

        current_set = set(current_expiries)
        prev_set = self.active_expiries[underlying]

        added = current_set - prev_set
        removed = prev_set - current_set

        if added:
            logger.info(f"[EXPIRY] {underlying}: new expiry(s) added: {sorted(added)}")
        if removed:
            logger.info(f"[EXPIRY] {underlying}: expiry(s) dropped: {sorted(removed)}")
            self.expired[underlying].update(removed)

        self.active_expiries[underlying] = current_set
        self.last_check = datetime.now()

        return {"added": sorted(added), "expired": sorted(removed)}

    def get_active_expiries(self, underlying):
        """Get set of currently active expiries for an underlying."""
        return self.active_expiries.get(underlying, set())

    def check_post_expiry_cleanup(self, underlying):
        """
        After 3:30 PM on expiry day, identify contracts to drop.

        Returns:
            list of expiry dates to drop
        """
        if not is_post_expiry_cutoff():
            return []

        today = dt_date.today()
        to_drop = []

        for exp in list(self.active_expiries.get(underlying, set())):
            if exp <= today:
                to_drop.append(exp)

        return to_drop

    def reset(self):
        """Full reset for testing."""
        self.active_expiries.clear()
        self.expired.clear()
        self.last_check = None


# Module-level singleton
_rollover_state = ExpiryRolloverState()


def get_rollover_state():
    """Get the module-level rollover state instance."""
    return _rollover_state


def reset_rollover_state():
    """Reset rollover state (for testing)."""
    _rollover_state.reset()
