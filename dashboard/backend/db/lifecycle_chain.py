"""
dashboard/backend/db/lifecycle_chain.py
=======================================
Cross-engine chain attribution.

One research idea can be picked up by more than one book — the same name may be
armed into Swing and later taken by Momentum on its own signal. Without a link
between those rows you can count trades but you cannot answer the question that
actually matters: *what happened to the ideas we published, and which engine
converted them best?*

Linking is deliberately conservative. A wrong link invents a causal story the
data does not support, so a position is only attached to an idea when all three
hold:

  * same symbol
  * the position opened AFTER the idea was published, within LOOKBACK_DAYS
  * the position's entry is within ENTRY_TOLERANCE_PCT of the idea's entry

Anything ambiguous is left unlinked and stays its own single-stage chain. An
unlinked position is still a complete record; it simply doesn't claim a parent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

from .schema import get_connection
from .trade_lifecycle import init_lifecycle_db

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

LOOKBACK_DAYS = int(os.getenv("LIFECYCLE_CHAIN_LOOKBACK_DAYS", "45"))
ENTRY_TOLERANCE_PCT = float(os.getenv("LIFECYCLE_CHAIN_ENTRY_TOLERANCE_PCT", "5.0"))


def _dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace(" ", "T").replace("Z", "+00:00"))
        return d.replace(tzinfo=_IST) if d.tzinfo is None else d
    except (TypeError, ValueError):
        return None


def link_chains(dry_run: bool = False) -> dict:
    """Attach POSITION rows to the IDEA that plausibly produced them.

    Idempotent: re-running re-evaluates every position and rewrites the same
    links, so it is safe on each backfill.
    """
    init_lifecycle_db()
    conn = get_connection()
    linked = skipped = ambiguous = 0
    try:
        ideas = [dict(r) for r in conn.execute(
            "SELECT uuid, symbol, entry_price, idea_at, created_at, engine "
            "FROM trade_lifecycle WHERE stage = 'IDEA' ORDER BY datetime(created_at) ASC"
        ).fetchall()]
        by_symbol: dict[str, list] = {}
        for i in ideas:
            by_symbol.setdefault(str(i["symbol"]).upper(), []).append(i)

        positions = [dict(r) for r in conn.execute(
            "SELECT uuid, symbol, entry_price, entry_fill_at, idea_at, created_at "
            "FROM trade_lifecycle WHERE stage = 'POSITION'"
        ).fetchall()]

        for p in positions:
            cands = by_symbol.get(str(p["symbol"]).upper(), [])
            if not cands:
                skipped += 1
                continue
            opened = _dt(p["entry_fill_at"]) or _dt(p["created_at"])
            pe = p["entry_price"]
            matches = []
            for i in cands:
                pub = _dt(i["idea_at"]) or _dt(i["created_at"])
                if not pub or not opened or opened < pub:
                    continue
                if (opened - pub).days > LOOKBACK_DAYS:
                    continue
                ie = i["entry_price"]
                if pe and ie and ie > 0:
                    if abs(pe - ie) / ie * 100 > ENTRY_TOLERANCE_PCT:
                        continue
                matches.append((abs((opened - pub).total_seconds()), i))
            if not matches:
                skipped += 1
                continue
            # Nearest publication in time wins; a tie is genuinely ambiguous and
            # would be a guess, so it is recorded and left unlinked.
            matches.sort(key=lambda m: m[0])
            if len(matches) > 1 and matches[0][0] == matches[1][0]:
                ambiguous += 1
                continue
            parent = matches[0][1]
            if not dry_run:
                conn.execute(
                    "UPDATE trade_lifecycle SET parent_id = ?, chain_id = ? WHERE uuid = ?",
                    (parent["uuid"], parent["uuid"], p["uuid"]),
                )
                conn.execute(
                    "UPDATE trade_lifecycle SET chain_id = ? WHERE uuid = ?",
                    (parent["uuid"], parent["uuid"]),
                )
            linked += 1

        if not dry_run:
            conn.commit()
        return {"ok": True, "dry_run": dry_run, "linked": linked,
                "unlinked": skipped, "ambiguous_left_unlinked": ambiguous,
                "lookback_days": LOOKBACK_DAYS,
                "entry_tolerance_pct": ENTRY_TOLERANCE_PCT}
    except Exception as exc:
        logger.error("[LifecycleChain] link failed: %s", exc, exc_info=True)
        return {"ok": False, "reason": str(exc), "linked": linked}
    finally:
        conn.close()


def chain(chain_id: str) -> dict:
    """Every stage of one idea's life, across whichever engines picked it up."""
    init_lifecycle_db()
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM trade_lifecycle WHERE chain_id = ? OR uuid = ? "
            "ORDER BY CASE stage WHEN 'IDEA' THEN 0 ELSE 1 END, datetime(created_at) ASC",
            (chain_id, chain_id),
        ).fetchall()]
        engines = sorted({r["portfolio"] or r["source"] for r in rows if r["stage"] == "POSITION"})
        return {
            "chain_id": chain_id,
            "stages": rows,
            "engines_that_traded_it": engines,
            "converted": bool(engines),
        }
    finally:
        conn.close()


def cross_engine_attribution() -> dict:
    """How each engine converts published ideas into outcomes.

    Only chains that actually contain an idea are counted, so an engine that
    trades entirely off its own signals is not credited with converting research
    it never saw.
    """
    init_lifecycle_db()
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT chain_id, stage, portfolio, source, status, pnl_pct, engine_version "
            "FROM trade_lifecycle WHERE COALESCE(record_state,'ACTIVE') = 'ACTIVE' "
            "AND is_duplicate = 0"
        ).fetchall()]

        chains: dict[str, dict] = {}
        for r in rows:
            cid = r["chain_id"] or "-"
            c = chains.setdefault(cid, {"has_idea": False, "positions": []})
            if r["stage"] == "IDEA":
                c["has_idea"] = True
            else:
                c["positions"].append(r)

        with_idea = [c for c in chains.values() if c["has_idea"]]
        converted = [c for c in with_idea if c["positions"]]
        multi = [c for c in converted if len({p["portfolio"] or p["source"] for p in c["positions"]}) > 1]

        per_engine: dict[str, dict] = {}
        for c in converted:
            for p in c["positions"]:
                k = p["portfolio"] or p["source"] or "UNKNOWN"
                e = per_engine.setdefault(k, {"engine": k, "converted": 0, "wins": 0,
                                              "closed": 0, "sum_pnl": 0.0})
                e["converted"] += 1
                if p["status"] in ("TARGET_HIT", "STOP_HIT", "TIME_EXIT",
                                   "FORCED_EXIT", "MANUAL_CLOSED"):
                    e["closed"] += 1
                    if (p["pnl_pct"] or 0) > 0:
                        e["wins"] += 1
                    e["sum_pnl"] += p["pnl_pct"] or 0.0
        for e in per_engine.values():
            e["win_rate_pct"] = round(e["wins"] / e["closed"] * 100, 1) if e["closed"] else 0.0
            e["sum_pnl"] = round(e["sum_pnl"], 2)

        return {
            "ideas_with_a_chain": len(with_idea),
            "ideas_converted_to_a_position": len(converted),
            "conversion_pct": round(len(converted) / len(with_idea) * 100, 1) if with_idea else 0.0,
            "ideas_traded_by_more_than_one_engine": len(multi),
            "per_engine": sorted(per_engine.values(), key=lambda x: -x["converted"]),
            "basis": ("only chains containing a published idea are counted, so an engine "
                      "trading its own signals is not credited with converting research"),
        }
    finally:
        conn.close()
