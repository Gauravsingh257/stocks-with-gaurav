"""
services/entry_gate.py
======================
THE authoritative gate between a candidate and the entry-monitoring lifecycle.

WHY THIS EXISTS
---------------
Forensics on the phantom "Entry Triggered" alerts (TARSONS, HFCL, JSFB,
APOLLOHOSP, ARVIND, PARKHOSPS, NIVABUPA, MINDACORP) established that the alert
payloads trace back to `signals_log` rows whose selector verdict was
`final_selected = 0` — REJECTED candidates — while the symbols had no row in
`portfolio_positions` under any status. So a rejected scan candidate was able to
reach entry monitoring and fire a user-facing alert claiming a position that the
book never held.

That is an architecture defect, not a formatting bug: entry monitoring was
trusting whatever its caller handed it rather than the Portfolio.

RELATED BUT DISTINCT: services/admission_gate.py
------------------------------------------------
`admission_gate` decides whether a candidate MAY BE ADMITTED to the book (a
policy judgement on price / turnover / ATR / stop width, applied before the
portfolio row is created, currently shadow-only). This module decides whether a
symbol IS ALREADY ADMITTED (a fact about the database, applied after the row
exists, enforcing). Upstream vs downstream — they cannot disagree, because this
gate only ever runs once admission_gate's decision has already been written as a
row or not written at all.

THE INVARIANT
-------------
    Nothing may be armed, monitored for entry, or alerted on unless a REAL row
    exists in the Portfolio for that symbol + book, in a state that legitimately
    precedes or constitutes an entry.

Deliberately NOT accepted as evidence of admission:
    symbol existence · signals_log presence · final_selected · engine name ·
    horizon · a cached/pending setup dict · watchlist membership · scanner output

The ONLY evidence is a `portfolio_positions` row. Every entry-monitoring path
calls the same function here, so there is one gate rather than one check per
caller — the previous per-caller approach is exactly how a door was missed.

WHY IT RE-READS THE DATABASE
----------------------------
Callers pass dicts they believe describe a position. The phantom alerts prove
that belief can be wrong. The gate therefore ignores the caller's payload and
asks the database directly. A caller holding a genuine position passes; a caller
holding a rejected scan candidate does not, regardless of how well-formed its
dict looks.

FAIL-CLOSED
-----------
If admission cannot be established the answer is NO. An unsent alert is a minor
loss; an alert describing a trade that does not exist is a correctness failure
the user cannot audit. This is the opposite of the older suppression guard,
which failed open on a database error — that guard was a filter on the symptom,
this is a gate on the lifecycle.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("services.entry_gate")

# States in which a Portfolio row legitimately participates in entry monitoring.
# PENDING  = admitted and armed, awaiting its entry to trade through.
# ACTIVE   = admitted and filled; re-alerting is idempotent, not phantom.
MONITORABLE_STATES = ("PENDING", "ACTIVE")

REASON_OK = "portfolio_admitted"
REASON_NO_ROW = "no_portfolio_admission"
REASON_BAD_STATE = "portfolio_row_not_monitorable"
REASON_BAD_INPUT = "invalid_symbol_or_book"
REASON_LOOKUP_FAILED = "admission_lookup_failed"


def enforcing() -> bool:
    """Hard gate on by default. `ENTRY_GATE_ENFORCE=0` downgrades to log-only,
    kept solely as an emergency escape hatch — it is read live so it can be
    flipped without a redeploy."""
    return os.getenv("ENTRY_GATE_ENFORCE", "1").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AdmissionCheck:
    admitted: bool
    reason: str
    symbol: str
    book: str
    status: str | None = None
    position_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.admitted


def _norm(symbol: str) -> tuple[str, str]:
    """Both spellings — the books store `NSE:X`, callers pass either."""
    s = str(symbol or "").strip().upper()
    bare = s.replace("NSE:", "")
    return f"NSE:{bare}", bare


def is_portfolio_admitted(symbol: str, book: str,
                          states: tuple[str, ...] = MONITORABLE_STATES) -> AdmissionCheck:
    """Does the Portfolio actually hold an admitted row for this symbol + book?

    This is the single source of truth for execution eligibility. It reads
    `portfolio_positions` (or `momentum_positions` for the momentum book) and
    nothing else.
    """
    prefixed, bare = _norm(symbol)
    bk = str(book or "").strip().upper()
    if not bare or bk not in ("SWING", "LONGTERM", "MOMENTUM"):
        return AdmissionCheck(False, REASON_BAD_INPUT, prefixed, bk)

    try:
        from dashboard.backend.db.schema import get_connection
        conn = get_connection()
        try:
            placeholders = ",".join("?" * len(states))
            if bk == "MOMENTUM":
                row = conn.execute(
                    f"SELECT id, status FROM momentum_positions "
                    f"WHERE UPPER(symbol) IN (?, ?) AND status IN ({placeholders}) LIMIT 1",
                    (prefixed, bare, *states),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT id, status FROM portfolio_positions "
                    f"WHERE UPPER(symbol) IN (?, ?) AND horizon = ? "
                    f"AND status IN ({placeholders}) LIMIT 1",
                    (prefixed, bare, bk, *states),
                ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        # Fail CLOSED. A lookup failure is not evidence of admission.
        log.error("[EntryGate] admission lookup failed for %s/%s (%s) — treating as NOT admitted",
                  prefixed, bk, exc)
        return AdmissionCheck(False, REASON_LOOKUP_FAILED, prefixed, bk,
                              details={"error": str(exc)})

    if not row:
        return AdmissionCheck(False, REASON_NO_ROW, prefixed, bk)
    status = row["status"] if hasattr(row, "keys") else row[1]
    pid = row["id"] if hasattr(row, "keys") else row[0]
    return AdmissionCheck(True, REASON_OK, prefixed, bk, status=status, position_id=int(pid))


def can_monitor_entry(symbol: str, book: str, *, source: str = "unknown",
                      states: tuple[str, ...] = MONITORABLE_STATES) -> bool:
    """The gate every entry-monitoring path must call.

    Returns True only when the Portfolio has admitted this symbol. A block is
    logged at WARNING with the calling path, so a blocked candidate is visible
    and attributable rather than silently dropped.
    """
    check = is_portfolio_admitted(symbol, book, states=states)
    if check.admitted:
        return True

    log.warning("[EntryGate] BLOCKED %s (%s) — %s · source=%s%s",
                check.symbol, check.book, check.reason, source,
                "" if enforcing() else " [LOG-ONLY: ENTRY_GATE_ENFORCE=0, allowing]")
    return not enforcing()


def require_admission(symbol: str, book: str, *, source: str = "unknown") -> None:
    """Raise for callers that should abort rather than skip.

    Used where continuing without admission would corrupt state, as opposed to
    the loop paths that simply skip the candidate.
    """
    if not can_monitor_entry(symbol, book, source=source):
        raise PermissionError(
            f"{symbol} ({book}) has no Portfolio admission — refusing to monitor/alert "
            f"[source={source}]"
        )
