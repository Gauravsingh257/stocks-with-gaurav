"""
dashboard/backend/db/position_provenance.py
===========================================
Immutable, write-time provenance for every portfolio position.

WHY THIS EXISTS
The 2026-09-13 audit could measure risk mechanics but could not answer "is the
engine picking better stocks?", because the evidence needed had never been
persisted at the moment a position was created:

  • `trade_lifecycle.algorithm_hash` was a SINGLE value across all 143 SWING/LT
    rows and `engine_version` likewise — both stamped during a later *backfill*
    from whatever the environment happened to be that day, so they recorded the
    present, not the configuration a trade was actually taken under.
  • `setup`, `recommendation_json` and `strategy_version` were 0/143.
  • The `recommendation_id` link is broken by row recycling: position ids reach
    1,045,380 while the immutable ledger covers 206-519, so only 26/155 resolve.

So this table records, AT WRITE TIME, what a later audit cannot reconstruct.

THE DESIGN RULE: never store a pointer to something mutable.
`stock_recommendations` rows are recycled, so a foreign key into them decays
into a false link — worse than no link, because it still resolves. Everything
here is either a value copied at capture time or a key into an append-only
table (`signals_log`), never a reference to a row that can be rewritten.

`scan_id` is the single most valuable field. `signals_log` already stores the
WHOLE candidate universe for each scan — every symbol's confidence, layer1/2/3
passes, `final_selected`, `rejection_reason` and `layer_details` (the SMC
evidence). It was always durable; positions simply never recorded which scan
produced them. With `scan_id` captured, "why was this stock selected over the
alternatives available at that time?" becomes answerable by joining back to the
scan — for every future trade.

Best-effort and non-blocking by construction: `capture()` never raises and
never alters the position write. A position must never fail to be created
because provenance could not be resolved. What cannot be resolved is recorded
in `missing` as an explicit list, so a future audit sees a documented gap
instead of silently trusting an absent field.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

SCHEMA = """
CREATE TABLE IF NOT EXISTS position_provenance (
    provenance_uuid   TEXT PRIMARY KEY,
    position_id       INTEGER NOT NULL,
    symbol            TEXT NOT NULL,
    horizon           TEXT NOT NULL,
    captured_at       TEXT NOT NULL,
    -- configuration in force AT WRITE TIME (never backfilled)
    engine_version    TEXT,
    algorithm_hash    TEXT,
    flags_json        TEXT,
    -- selection provenance
    source_door       TEXT,
    scan_id           TEXT,
    signals_log_id    INTEGER,
    setup             TEXT,
    entry_type        TEXT,
    confidence        REAL,
    rank_in_scan      INTEGER,
    selected_count    INTEGER,
    universe_scanned  INTEGER,
    smc_evidence      TEXT,
    -- geometry at entry
    entry_price       REAL,
    stop_loss         REAL,
    target_1          REAL,
    stop_width_pct    REAL,
    rr_at_entry       REAL,
    entry_gap_pct     REAL,
    -- context
    sector            TEXT,
    regime            TEXT,
    -- immutable snapshot of the recommendation as it read at capture time
    recommendation_snapshot TEXT,
    recommendation_id INTEGER,
    missing           TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_prov_position ON position_provenance(position_id);
CREATE INDEX IF NOT EXISTS idx_prov_symbol   ON position_provenance(symbol, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_prov_scan     ON position_provenance(scan_id);
"""

# Flags whose value changes what "the engine" means. Captured verbatim so a
# cohort can be defined by CONFIGURATION rather than by date — the thing the
# 2026-09-13 audit had to approximate with capability markers.
_PROVENANCE_FLAGS = (
    "ENTRY_ANCHOR_MAX_GAP_PCT", "PHASE1_TIGHT_ENTRY_GAP", "PHASE1_ENTRY_GAP_PCT",
    "PHASE0_KITE_OHLC", "PHASE0_NO_SYNTHETIC", "PHASE0_REAL_SECTORS",
    "PHASE1_STRICT_FUNNEL", "PHASE1_UNIFIED_FEED", "PHASE1_SECTOR_UNKNOWN_STRICT",
    "PHASE2_SMC_AS_SCORE", "PHASE2_HORIZONS", "EXCEPTIONALISM_ENABLED",
    "REGIME_GOVERNOR_ENABLED", "SECTOR_LEADERSHIP_SCORING_ENABLED",
    "SECTOR_DIVERSIFICATION_ENABLED", "RISK_ENGINE_ENABLED", "TREND_BREAK_EXIT_ENABLED",
    "STRUCTURAL_TARGET_CAP", "PORTFOLIO_STRUCTURE_EXIT", "PORTFOLIO_STALE_EXIT",
    "PORTFOLIO_STALE_EXIT_INDEPENDENT", "ADMISSION_GATE_ENFORCE",
)


def init_provenance_db(conn=None) -> None:
    own = conn is None
    if own:
        from .schema import get_connection
        conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        if own:
            conn.commit()
    except Exception:
        logger.debug("[provenance] init failed", exc_info=True)
    finally:
        if own:
            with contextlib.suppress(Exception):
                conn.close()


def _flags() -> dict:
    return {k: os.getenv(k, "") for k in _PROVENANCE_FLAGS}


def _algorithm_hash(flags: dict, engine_version: str) -> str:
    """Fingerprint of the configuration this position was actually taken under.

    Unlike lifecycle_capture.algorithm_hash — which is applied retroactively and
    therefore fingerprints whatever the environment is at backfill time — this is
    computed at capture and stored, so two positions differ here exactly when the
    rules that produced them differed."""
    import hashlib
    parts = [engine_version] + [f"{k}={flags.get(k, '')}" for k in sorted(flags)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _latest_signal_row(conn, symbol: str, horizon: str) -> dict | None:
    """The most recent scan row for this symbol — the selection evidence.

    `signals_log` is append-only, so this is a durable key, not a decaying
    pointer. Matches on the bare symbol because callers are inconsistent about
    the `NSE:` prefix."""
    try:
        bare = str(symbol or "").replace("NSE:", "").strip().upper()
        return dict(conn.execute(
            "SELECT id, scan_id, confidence, entry, stop_loss, target, cmp, "
            "layer_details, final_selected, rejection_reason, date "
            "FROM signals_log WHERE REPLACE(UPPER(symbol),'NSE:','') = ? AND horizon = ? "
            "ORDER BY created_at DESC LIMIT 1", (bare, str(horizon).upper())
        ).fetchone() or {}) or None
    except Exception:
        return None


def _scan_shape(conn, scan_id: str) -> tuple[int | None, int | None]:
    """(selected_count, universe_scanned) for the scan — the denominator behind
    'this stock was chosen over N alternatives'."""
    try:
        r = conn.execute(
            "SELECT SUM(final_selected) AS sel, COUNT(*) AS tot FROM signals_log WHERE scan_id = ?",
            (scan_id,)).fetchone()
        return (int(r["sel"]) if r and r["sel"] is not None else None,
                int(r["tot"]) if r and r["tot"] is not None else None)
    except Exception:
        return (None, None)


def _sector(symbol: str) -> str | None:
    try:
        from services.portfolio_risk import get_sector
        return get_sector(str(symbol or "").replace("NSE:", "")) or None
    except Exception:
        return None


def _regime() -> str | None:
    try:
        from services.regime_governor import current_regime
        r = current_regime()
        if isinstance(r, dict):
            return r.get("regime") or r.get("state") or None
        return str(r) if r else None
    except Exception:
        return None


def capture(position_id: int, payload: dict, conn=None) -> str | None:
    """Record provenance for a freshly-created position. Never raises.

    Returns the provenance uuid, or None if nothing could be written. The
    caller must not branch on the result — a position is valid whether or not
    its provenance resolved."""
    try:
        own = conn is None
        if own:
            from .schema import get_connection
            conn = get_connection()
        init_provenance_db(conn)

        symbol = str(payload.get("symbol") or "")
        horizon = str(payload.get("horizon") or "SWING").upper()
        missing: list[str] = []

        flags = _flags()
        engine_version = os.getenv("SMC_ENGINE_VERSION", "SMC v4.2.1")

        sig = _latest_signal_row(conn, symbol, horizon)
        if not sig:
            missing.append("signals_log_row")
        scan_id = (sig or {}).get("scan_id")
        sel_count, uni = _scan_shape(conn, scan_id) if scan_id else (None, None)

        smc = None
        setup = None
        entry_type = None
        if sig:
            try:
                ld = json.loads(sig.get("layer_details") or "{}")
                smc = (ld or {}).get("smc")
                entry_type = (smc or {}).get("entry_type")
                setup = (ld or {}).get("setup") or (smc or {}).get("setup")
            except Exception:
                pass
        if not smc:
            missing.append("smc_evidence")

        e = payload.get("entry_price")
        s = payload.get("stop_loss")
        t = payload.get("target_1")
        cmp_ = payload.get("current_price") or payload.get("arm_ref_price")
        try:
            e, s = float(e), float(s)
            stop_w = (e - s) / e * 100 if e and s < e else None
            rr = (float(t) - e) / (e - s) if (t and e > s) else None
        except (TypeError, ValueError):
            stop_w = rr = None
        try:
            gap = abs(float(cmp_) - float(e)) / float(e) * 100 if cmp_ and e else None
        except (TypeError, ValueError, ZeroDivisionError):
            gap = None
        if gap is None:
            missing.append("entry_gap_pct")

        sector = _sector(symbol)
        if not sector:
            missing.append("sector")
        regime = _regime()
        if not regime:
            missing.append("regime")

        # Immutable copy — NOT a reference. stock_recommendations rows recycle.
        snapshot = {k: payload.get(k) for k in
                    ("confidence_score", "reasoning", "recommendation_id",
                     "entry_price", "stop_loss", "target_1", "target_2",
                     "current_price", "arm_ref_price", "position_size",
                     "risk_weight_pct", "atr_pct", "turnover_cr", "status")}
        if sig:
            snapshot["scan_confidence"] = sig.get("confidence")
            snapshot["scan_cmp"] = sig.get("cmp")
            snapshot["scan_entry"] = sig.get("entry")
            snapshot["scan_date"] = sig.get("date")
            snapshot["final_selected"] = sig.get("final_selected")

        pid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO position_provenance ("
            "provenance_uuid, position_id, symbol, horizon, captured_at, engine_version, "
            "algorithm_hash, flags_json, source_door, scan_id, signals_log_id, setup, "
            "entry_type, confidence, rank_in_scan, selected_count, universe_scanned, "
            "smc_evidence, entry_price, stop_loss, target_1, stop_width_pct, rr_at_entry, "
            "entry_gap_pct, sector, regime, recommendation_snapshot, recommendation_id, missing"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, int(position_id), symbol, horizon, datetime.now(_IST).isoformat(),
             engine_version, _algorithm_hash(flags, engine_version),
             json.dumps(flags, default=str), payload.get("source_door"),
             scan_id, (sig or {}).get("id"), setup, entry_type,
             payload.get("confidence_score"), None, sel_count, uni,
             json.dumps(smc, default=str) if smc else None,
             e if isinstance(e, float) else None, s if isinstance(s, float) else None,
             float(t) if t else None,
             round(stop_w, 3) if stop_w is not None else None,
             round(rr, 3) if rr is not None else None,
             round(gap, 3) if gap is not None else None,
             sector, regime, json.dumps(snapshot, default=str),
             payload.get("recommendation_id"), json.dumps(missing)),
        )
        if own:
            conn.commit()
            with contextlib.suppress(Exception):
                conn.close()
        logger.info("[provenance] %s/%s pos=%s scan=%s missing=%s",
                    symbol, horizon, position_id, scan_id, missing or "-")
        return pid
    except Exception:
        logger.debug("[provenance] capture failed for position %s", position_id, exc_info=True)
        return None
