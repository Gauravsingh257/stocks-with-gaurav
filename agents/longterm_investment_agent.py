from __future__ import annotations

import asyncio
import os

from agents.base import BaseAgent, AgentResult
from dashboard.backend.db import create_stock_recommendation, log_ranking_run, get_stock_recommendations
from services import generate_rankings
from services.portfolio_constructor import apply_sector_cap

MAX_LONGTERM_SLOTS = int(__import__("os").getenv("MAX_LONGTERM_SLOTS", "25"))
RESEARCH_AGENT_TARGET_UNIVERSE = int(os.getenv("RESEARCH_AGENT_TARGET_UNIVERSE", "2200"))


class LongTermInvestmentAgent(BaseAgent):
    name = "LongTermInvestmentAgent"
    description = "Monthly long-term conviction ideas with thesis and risk map."
    schedule = "Weekly scan (Mon 08:40 IST)"
    priority = "high"

    def run(self, result: AgentResult) -> None:
        # Mirror of SwingTradeAlphaAgent: the legacy slot machine has an early
        # return (slots full). _research_feed_tick — the LONGTERM validation
        # log (signals_log) + coverage log (ranking_runs) — MUST run on every
        # invocation regardless of slot state, so it lives in finally{} and is
        # never starved by that guard.
        try:
            self._run_legacy(result)
        finally:
            self._research_feed_tick(result)

    def _run_legacy(self, result: AgentResult) -> None:
        # Slot-aware scanning: only fill empty slots (unless force-scan flag is set)
        force_scan = __import__("os").environ.pop("LONGTERM_FORCE_SCAN", "").strip() == "1"
        active_recs = get_stock_recommendations("LONGTERM", limit=MAX_LONGTERM_SLOTS)
        active_count = len(active_recs)
        empty_slots = MAX_LONGTERM_SLOTS - active_count
        active_symbols = [r["symbol"] for r in active_recs]

        # Cross-horizon dedup — symbols already shown as SWING picks should not
        # also surface as LONGTERM picks in the same scan window.
        try:
            swing_active = get_stock_recommendations("SWING", limit=50)
            cross_horizon_symbols = [r["symbol"] for r in swing_active]
        except Exception:
            cross_horizon_symbols = []
        exclude_set = list({*active_symbols, *cross_horizon_symbols})

        if empty_slots <= 0 and not force_scan:
            result.status = "OK"
            result.summary = f"All {MAX_LONGTERM_SLOTS} longterm slots occupied — scan skipped."
            result.metrics = {"active_slots": active_count, "empty_slots": 0}
            return

        # When forcing a scan with no empty slots, still pull a fresh top-K so
        # users see refreshed candidates ranked alongside the current active set.
        # Scan at least 15 candidates even when only a few slots are empty.
        scan_top_k = max(
            empty_slots if empty_slots > 0 else MAX_LONGTERM_SLOTS,
            int(os.getenv("MIN_LONGTERM_SCAN_K", "15")),
        )

        ranking = asyncio.run(generate_rankings(
            "LONGTERM", top_k=scan_top_k, target_universe=RESEARCH_AGENT_TARGET_UNIVERSE,
            exclude_symbols=exclude_set,
        ))

        # NOTE: the LONGTERM validation scan + signals_log write is no longer
        # done here — it moved to _research_feed_tick (run() finally{}) so it
        # is never starved by the slots-full early return above.

        # Log ranking run upfront to get scan_run_id for all recommendations
        run_id = log_ranking_run(
            horizon="LONGTERM",
            universe_requested=ranking.universe.requested_size,
            universe_scanned=ranking.scanned,
            quality_passed=ranking.quality_passed,
            ranked_candidates=ranking.ranked_candidates,
            selected_count=len(ranking.ideas),
            notes=f"sources={ranking.universe.sources}|ideas={len(ranking.ideas)}",
        )

        saved = 0
        findings: list[dict] = []
        active_symbols: list[str] = []
        # Phase 3.3: drop ideas exceeding sector cap
        capped_ideas = apply_sector_cap(ranking.ideas)
        for idea in capped_ideas:
            symbol = idea.symbol
            entry_low = idea.entry_price
            entry_high = idea.entry_zone[1] if idea.entry_zone and len(idea.entry_zone) > 1 else idea.entry_price
            fair_value = idea.fair_value_estimate
            long_target = idea.long_term_target or (idea.targets[0] if idea.targets else idea.entry_price)
            stop_loss = idea.stop_loss
            thesis = idea.reasoning
            risk_factors = idea.risk_factors or []

            # Entry type + CMP from scoring
            entry_type = getattr(idea, "entry_type", "MARKET") or "MARKET"
            scan_cmp = getattr(idea, "scan_cmp", None)

            # Confidence downgrade for LIMIT entries far from CMP
            confidence = idea.confidence_score
            if entry_type == "LIMIT" and scan_cmp and entry_low > 0:
                gap_pct = abs((scan_cmp - entry_low) / entry_low * 100)
                if gap_pct > 5:
                    confidence = round(confidence * 0.70, 2)
                elif gap_pct > 3:
                    confidence = round(confidence * 0.85, 2)

            row = {
                "symbol": symbol,
                "agent_type": "LONGTERM",
                "entry_price": entry_low,
                "stop_loss": stop_loss,
                "targets": idea.targets,
                "confidence_score": confidence,
                "technical_signals": idea.technical_signals,
                "fundamental_signals": idea.fundamental_signals,
                "sentiment_signals": idea.sentiment_signals,
                "fundamental_factors": idea.fundamental_factors,
                "technical_factors": idea.technical_factors,
                "sentiment_factors": idea.sentiment_factors,
                "fair_value_estimate": fair_value,
                "entry_zone": idea.entry_zone or [entry_low, entry_high],
                "long_term_target": long_target,
                "risk_factors": risk_factors,
                "expected_holding_period": idea.expected_holding_period,
                "reasoning": thesis,
                "data_authenticity": "real" if "SMC_LONGTERM" in (idea.setup or "") else "partial",
                "scan_run_id": run_id,
                "entry_type": entry_type,
                "scan_cmp": scan_cmp,
                "smc_evidence": getattr(idea, "smc_evidence", None),
                "sector": getattr(idea, "sector", None),
                "target_source": getattr(idea, "target_source", None),
            }
            rec_id = create_stock_recommendation(row)
            # G2-3 SHADOW: canonical FILTERED event (best-effort, never raises).
            try:
                from dashboard.backend.lifecycle_ledger import record_lifecycle_event

                record_lifecycle_event(
                    symbol, "FILTERED", horizon="LONGTERM", source="longterm_agent",
                    recommendation_id=rec_id if isinstance(rec_id, int) else None,
                    planned_entry=row.get("entry_price"), stop_loss=row.get("stop_loss"),
                    target_1=(row.get("targets") or [None])[0],
                    confidence=row.get("confidence_score"),
                    setup=row.get("setup"), sector=getattr(idea, "sector", None),
                )
            except Exception:
                pass
            if rec_id and rec_id > 0:
                saved += 1
                active_symbols.append(symbol)

            findings.append(
                {
                    "symbol": symbol,
                    "long_term_thesis": thesis,
                    "fair_value_estimate": fair_value,
                    "entry_zone": [entry_low, entry_high],
                    "long_term_target": long_target,
                    "risk_factors": risk_factors,
                    "time_horizon": idea.expected_holding_period,
                    "confidence_score": idea.confidence_score,
                    "technical_signals": idea.technical_signals,
                    "fundamental_signals": idea.fundamental_signals,
                    "sentiment_signals": idea.sentiment_signals,
                }
            )

        result.metrics = {
            "universe_scanned": ranking.scanned,
            "quality_passed": ranking.quality_passed,
            "ranked_candidates": ranking.ranked_candidates,
            "recommendations_saved": saved,
            "requested_universe_size": ranking.universe.requested_size,
            "actual_universe_size": ranking.universe.actual_size,
            "active_slots": active_count + saved,
            "empty_slots": empty_slots - saved,
        }

        result.findings = findings
        if saved == 0:
            result.status = "WARNING"
            result.summary = (
                f"Long-term scan finished: 0 ideas saved (scanned {ranking.scanned} symbols, "
                f"{ranking.quality_passed} quality pass, {ranking.ranked_candidates} ranked)."
            )
            return

        result.summary = f"Long-term ranking completed. Saved top {saved} names from {ranking.scanned} scanned symbols."

        try:
            from services.trade_tracker import seed_running_trades
            seed_running_trades()
        except Exception:
            pass

        # Auto-promote into persistent portfolio
        try:
            from services.idea_selector import select_and_promote
            promoted = select_and_promote("LONGTERM")
            if promoted:
                import logging as _log
                _log.getLogger("LongTermInvestmentAgent").info("Portfolio: promoted %d longterm positions", promoted)
        except Exception:
            pass

    def _research_feed_tick(self, result: AgentResult) -> None:
        # LONGTERM mirror of SwingTradeAlphaAgent._research_feed_tick. The
        # public LONGTERM research surface + its coverage number are fed only
        # by a logged LONGTERM validation scan (signals_log) plus a
        # ranking_runs row. Both used to sit BELOW _run_legacy's slots-full
        # early return and silently froze once the slots filled. Invoked from
        # run()'s finally{} so they refresh on EVERY invocation regardless of
        # slot state. Flag-gated by RESEARCH_AGENT_VALIDATION_LOG (byte-
        # identical when off) and fully exception-isolated.
        if os.getenv("RESEARCH_AGENT_VALIDATION_LOG", "1").strip().lower() not in (
            "1", "true", "yes",
        ):
            return
        try:
            from services.validation_engine import run_validation_scan

            scan_k = int(os.getenv("RESEARCH_FEED_SCAN_K", "25"))
            validation = asyncio.run(run_validation_scan(
                "LONGTERM",
                top_k=scan_k,
                target_universe=RESEARCH_AGENT_TARGET_UNIVERSE,
                log_scan=True,
            ))
            try:
                log_ranking_run(
                    horizon="LONGTERM",
                    universe_requested=validation.universe.requested_size,
                    universe_scanned=validation.coverage.scanned,
                    quality_passed=validation.funnel.layer1_pass,
                    ranked_candidates=validation.funnel.layer2_pass,
                    selected_count=validation.funnel.final_selected,
                    notes=f"research_feed_tick|scan_id={validation.scan_id}",
                )
            except Exception as exc:
                result.metrics = {**(result.metrics or {}),
                                  "research_feed_ranking_log_error": str(exc)}
            result.metrics = {**(result.metrics or {}), "research_feed": {
                "validation_logged_rows": validation.logged_rows,
                "scanned": validation.coverage.scanned,
                "final": validation.funnel.final_selected,
            }}
        except Exception as exc:
            result.metrics = {**(result.metrics or {}),
                              "research_feed_error": str(exc)}
