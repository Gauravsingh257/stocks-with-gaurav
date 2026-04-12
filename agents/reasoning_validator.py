"""
agents/reasoning_validator.py

Hourly reasoning re-validator for active stock recommendations.

For each ACTIVE recommendation:
1. Fetches fresh technical snapshot (OHLC via yfinance)
2. Fetches fresh fundamental + sentiment data
3. Re-generates reasoning from current signals
4. If reasoning has materially changed → updates the recommendation
5. Logs all changes to signal_events for audit trail

Runs every hour during market hours (09:00–16:00 Mon-Fri).
"""

from __future__ import annotations

import asyncio
import json
import logging

from agents.base import BaseAgent, AgentResult

log = logging.getLogger("agents.reasoning_validator")


class ReasoningValidatorAgent(BaseAgent):
    name = "ReasoningValidatorAgent"
    description = "Hourly reasoning re-validation for active recommendations."
    schedule = "Every 1h (09:00–16:00 IST)"
    priority = "medium"

    def run(self, result: AgentResult) -> None:
        validated, updated, failed = asyncio.run(_validate_all_active())
        result.metrics = {
            "validated": validated,
            "updated": updated,
            "failed": failed,
        }
        if updated > 0:
            result.summary = f"Reasoning validated for {validated} stocks — {updated} updated with fresh data."
        else:
            result.summary = f"Reasoning validated for {validated} active stocks — all current and accurate."


async def _validate_all_active() -> tuple[int, int, int]:
    """Validate reasoning for all ACTIVE recommendations. Returns (validated, updated, failed)."""
    from dashboard.backend.db import get_stock_recommendations, log_signal_event
    from dashboard.backend.db.schema import get_connection

    validated = 0
    updated = 0
    failed = 0

    for horizon in ("SWING", "LONGTERM"):
        rows = get_stock_recommendations(horizon, limit=50)
        if not rows:
            continue

        symbols = [r["symbol"] for r in rows]
        fresh_data = await _fetch_fresh_data(symbols)

        for row in rows:
            symbol = row["symbol"]
            if symbol not in fresh_data:
                failed += 1
                continue

            try:
                tech, fund, sent = fresh_data[symbol]
                new_reasoning, new_signals = _regenerate_reasoning(
                    symbol, horizon, tech, fund, sent
                )

                old_reasoning = row.get("reasoning", "")
                # Only update if reasoning materially changed (not just whitespace)
                if _reasoning_changed(old_reasoning, new_reasoning):
                    _update_recommendation_reasoning(
                        row["id"], symbol, new_reasoning, new_signals, horizon
                    )
                    log_signal_event(
                        symbol=symbol,
                        event_type="REASONING_REFRESHED",
                        source="reasoning_validator",
                        recommendation_id=row["id"],
                        details={
                            "horizon": horizon,
                            "old_reasoning_len": len(old_reasoning),
                            "new_reasoning_len": len(new_reasoning),
                        },
                    )
                    updated += 1
                    log.info("Reasoning updated for %s (%s)", symbol, horizon)

                validated += 1
            except Exception:
                log.exception("Reasoning validation failed for %s", symbol)
                failed += 1

    return validated, updated, failed


async def _fetch_fresh_data(symbols: list[str]) -> dict:
    """Fetch fresh technical, fundamental, sentiment data for symbols."""
    from services import scan_technical, analyze_fundamentals, analyze_news_sentiment

    result: dict = {}
    try:
        tech_map = await scan_technical(symbols)
        fund_map = await analyze_fundamentals(symbols)
        sent_map = await analyze_news_sentiment(symbols)

        for sym in symbols:
            tech = tech_map.get(sym)
            fund = fund_map.get(sym)
            sent = sent_map.get(sym)
            if tech and fund and sent:
                result[sym] = (tech, fund, sent)
    except Exception:
        log.exception("Fresh data fetch failed")

    return result


def _regenerate_reasoning(
    symbol: str,
    horizon: str,
    tech,
    fund,
    sent,
) -> tuple[str, dict]:
    """Re-generate reasoning from fresh data. Returns (reasoning_text, signal_dicts)."""
    from services import extract_swing_signals, extract_longterm_signals
    from services.reasoning_engine import generate_evidence_reasoning

    if horizon == "LONGTERM":
        evidence = extract_longterm_signals(symbol, tech, fund, sent)
    else:
        evidence = extract_swing_signals(symbol, tech, fund, sent)

    reasoning, _ = generate_evidence_reasoning(
        symbol,
        evidence.technical_signals,
        evidence.fundamental_signals,
        evidence.sentiment_signals,
        min_factors=3,
        max_factors=6,
    )

    return reasoning, {
        "technical_signals": evidence.technical_signals,
        "fundamental_signals": evidence.fundamental_signals,
        "sentiment_signals": evidence.sentiment_signals,
    }


def _reasoning_changed(old: str, new: str) -> bool:
    """Check if reasoning has materially changed (ignoring minor whitespace/formatting)."""
    old_clean = " ".join(old.split()).strip().lower()
    new_clean = " ".join(new.split()).strip().lower()
    if old_clean == new_clean:
        return False
    # Allow up to 5% character overlap to account for minor rounding differences
    if len(old_clean) > 0 and len(new_clean) > 0:
        # Use simple length-based heuristic for minor number changes
        shorter = min(len(old_clean), len(new_clean))
        longer = max(len(old_clean), len(new_clean))
        if shorter > 0 and (longer - shorter) / shorter < 0.03:
            # Very similar length — check if most words are the same
            old_words = set(old_clean.split())
            new_words = set(new_clean.split())
            common = len(old_words & new_words)
            total = len(old_words | new_words)
            if total > 0 and common / total > 0.90:
                return False  # 90%+ word overlap = not materially changed
    return True


def _update_recommendation_reasoning(
    rec_id: int,
    symbol: str,
    new_reasoning: str,
    new_signals: dict,
    horizon: str,
) -> None:
    """Update the recommendation's reasoning and signals in DB."""
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE stock_recommendations SET
                reasoning = ?,
                technical_signals = ?,
                fundamental_signals = ?,
                sentiment_signals = ?,
                signals_updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                new_reasoning,
                json.dumps(new_signals.get("technical_signals", {})),
                json.dumps(new_signals.get("fundamental_signals", {})),
                json.dumps(new_signals.get("sentiment_signals", {})),
                rec_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
