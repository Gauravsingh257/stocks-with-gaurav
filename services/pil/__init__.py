"""
services/pil/  —  Portfolio Intelligence Layer (PIL)
====================================================
A read-only PMS / Bloomberg-Terminal-style layer that sits ABOVE the three
independent trading engines (Swing, Long-Term, Momentum). It only observes,
measures, analyses and reports — it NEVER feeds a decision back into any engine.

Design rules (enforced by construction):
  * PIL reads engine data exclusively through the existing DB getters
    (dashboard.backend.db.portfolio / .momentum_portfolio). It never writes to
    an engine table and never imports an engine loop.
  * PIL writes only to its own `pil_*` tables (dashboard.backend.db.pil).
  * Everything is gated by PIL_ENABLED (+ sub-flags) and defaults OFF, so with
    the flag unset PIL is inert and the platform behaves exactly as before.

The Swing/LT books track P&L in % / per-share terms with no ₹ notional, so PIL
adds a configurable **book-capital accounting layer** (services/pil/accounting.py)
that reconstructs a virtual ledger from existing rows. This is pure accounting —
the engines are untouched.
"""

BOOKS = ("SWING", "LONGTERM", "MOMENTUM")
BOOK_LABELS = {
    "SWING": "Swing",
    "LONGTERM": "Long-Term",
    "MOMENTUM": "Momentum",
    "COMBINED": "Combined",
}
