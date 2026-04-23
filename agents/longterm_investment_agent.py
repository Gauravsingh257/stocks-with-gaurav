from __future__ import annotations

import asyncio

from agents.base import BaseAgent, AgentResult
from dashboard.backend.db import create_stock_recommendation, log_ranking_run, get_stock_recommendations
from services import generate_rankings
from services.portfolio_constructor import apply_sector_cap

MAX_LONGTERM_SLOTS = int(__import__("os").getenv("MAX_LONGTERM_SLOTS", "10"))


class LongTermInvestmentAgent(BaseAgent):
    name = "LongTermInvestmentAgent"
    description = "Monthly long-term conviction ideas with thesis and risk map."
    schedule = "Weekly scan (Mon 08:40 IST)"
    priority = "high"

    def run(self, result: AgentResult) -> None:
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
        scan_top_k = empty_slots if empty_slots > 0 else MAX_LONGTERM_SLOTS

        ranking = asyncio.run(generate_rankings(
            "LONGTERM", top_k=scan_top_k, target_universe=1800,
            exclude_symbols=exclude_set,
        ))

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
                "smc_evidence": idea.smc_evidence,
                "sector": idea.sector,
                "target_source": idea.target_source,
            }
            create_stock_recommendation(row)
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
