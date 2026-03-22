"""
content_engine/state/post_history.py

7-day rolling post history tracker.

Persists to a JSON file so state survives restarts and Railway redeploys.
Provides two public interfaces:

    PostHistory.record(strategy_id)        — log a strategy as posted today
    PostHistory.recently_posted(days=7)    — set of IDs posted in the last N days
    PostHistory.filter_unseen(candidates)  — remove recently-posted from a list

    NewsHistory.record(event_hash)         — log a news item (by content hash)
    NewsHistory.is_seen(event_hash)        — True if seen in last N days
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("content_engine.post_history")

_HERE     = Path(__file__).parent          # content_engine/state/
_STATE_DIR = _HERE                         # store JSON alongside this module
_STATE_DIR.mkdir(parents=True, exist_ok=True)

_STRATEGY_HISTORY_FILE = _STATE_DIR / "strategy_history.json"
_NEWS_HISTORY_FILE     = _STATE_DIR / "news_history.json"
_RETENTION_DAYS        = 7


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _load(path: Path) -> list[dict[str, Any]]:
    """Load history list from JSON, returning [] on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _cutoff(days: int) -> datetime:
    return datetime.now() - timedelta(days=days)


def _prune(records: list[dict], days: int) -> list[dict]:
    """Remove entries older than `days`."""
    cutoff = _cutoff(days).isoformat()
    return [r for r in records if r.get("ts", "") >= cutoff]


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

class PostHistory:
    """
    Track which strategy IDs have been posted recently.

    Usage:
        h = PostHistory()
        h.record("order_block")
        unseen = h.filter_unseen(["order_block", "fvg", "vwap_strategy"])
        # → ["fvg", "vwap_strategy"]
    """

    def __init__(self, retention_days: int = _RETENTION_DAYS) -> None:
        self._path      = _STRATEGY_HISTORY_FILE
        self._retention = retention_days
        self._records   = _prune(_load(self._path), retention_days)

    # ── writes ────────────────────────────────────────────────────────────────

    def record(self, strategy_id: str) -> None:
        """Mark a strategy as posted right now."""
        self._records.append({"id": strategy_id, "ts": datetime.now().isoformat()})
        self._persist()
        log.debug("PostHistory: recorded %s", strategy_id)

    def record_batch(self, strategy_ids: list[str]) -> None:
        """Record multiple strategies at once (e.g. after a successful run)."""
        now = datetime.now().isoformat()
        for sid in strategy_ids:
            self._records.append({"id": sid, "ts": now})
        self._persist()
        log.info("PostHistory: recorded %d strategies", len(strategy_ids))

    # ── reads ─────────────────────────────────────────────────────────────────

    def recently_posted(self, days: int | None = None) -> set[str]:
        """Return set of strategy IDs posted within `days` days."""
        days = days or self._retention
        cutoff = _cutoff(days).isoformat()
        return {r["id"] for r in self._records if r.get("ts", "") >= cutoff}

    def filter_unseen(
        self,
        candidates: list[dict[str, Any]],
        id_key: str = "id",
        days: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return candidates not recently posted.
        Falls back to full list if all candidates were recently posted
        (prevents zero-selection on small strategy pools).
        """
        seen = self.recently_posted(days)
        unseen = [c for c in candidates if c.get(id_key) not in seen]
        if not unseen:
            log.info("PostHistory: all candidates seen — resetting filter for today")
            # Clear only today's records so tomorrow stays protected
            self._records = [r for r in self._records
                             if r.get("ts", "")[:10] != datetime.now().date().isoformat()]
            self._persist()
            return candidates  # return full list
        return unseen

    def posted_today(self) -> set[str]:
        """Return strategy IDs posted today only."""
        today = datetime.now().date().isoformat()
        return {r["id"] for r in self._records if r.get("ts", "")[:10] == today}

    # ── internal ──────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        self._records = _prune(self._records, self._retention)
        try:
            _save(self._path, self._records)
        except Exception as exc:
            log.warning("PostHistory: could not persist: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# NEWS HISTORY — dedup news by content hash
# ═══════════════════════════════════════════════════════════════════════════════

def _news_hash(event: str) -> str:
    """Stable 12-char hash of the event string (lowercased, stripped)."""
    clean = event.lower().strip()
    return hashlib.sha256(clean.encode()).hexdigest()[:12]


class NewsHistory:
    """
    Prevent the same news story being posted twice within `retention_days`.

    Usage:
        nh = NewsHistory()
        if not nh.is_seen(item["event"]):
            nh.record(item["event"])
            # send the post
    """

    def __init__(self, retention_days: int = _RETENTION_DAYS) -> None:
        self._path      = _NEWS_HISTORY_FILE
        self._retention = retention_days
        self._records   = _prune(_load(self._path), retention_days)
        self._seen_set  = {r["hash"] for r in self._records}

    def record(self, event: str) -> None:
        h = _news_hash(event)
        self._records.append({"hash": h, "ts": datetime.now().isoformat()})
        self._seen_set.add(h)
        self._persist()

    def is_seen(self, event: str, days: int | None = None) -> bool:
        """True if this event was already posted within `days` days."""
        h = _news_hash(event)
        if h not in self._seen_set:
            return False
        days = days or self._retention
        cutoff = _cutoff(days).isoformat()
        return any(r["hash"] == h and r.get("ts", "") >= cutoff for r in self._records)

    def filter_unseen(self, items: list[dict[str, Any]], event_key: str = "event") -> list[dict[str, Any]]:
        """Return only items whose event string has not been seen recently."""
        return [item for item in items if not self.is_seen(item.get(event_key, ""))]

    def _persist(self) -> None:
        self._records = _prune(self._records, self._retention)
        try:
            _save(self._path, self._records)
        except Exception as exc:
            log.warning("NewsHistory: could not persist: %s", exc)
