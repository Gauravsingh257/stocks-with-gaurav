from __future__ import annotations

import asyncio

from agents.base import BaseAgent, AgentResult
from dashboard.backend.db import create_stock_recommendation, log_ranking_run, archive_stale_recommendations
from services import generate_rankings


class LongTermInvestmentAgent(BaseAgent):
    name = "LongTermInvestmentAgent"
    description = "Monthly long-term conviction ideas with thesis and risk map."
    schedule = "Weekly scan (Mon 08:40 IST)"
    priority = "high"

    def run(self, result: AgentResult) -> None:
        ranking = asyncio.run(generate_rankings("LONGTERM", top_k=8, target_universe=1800))

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
        for idea in ranking.ideas:
            symbol = idea.symbol
            entry_low = idea.entry_price
            entry_high = idea.entry_zone[1] if idea.entry_zone and len(idea.entry_zone) > 1 else idea.entry_price
            fair_value = idea.fair_value_estimate
            long_target = idea.long_term_target or (idea.targets[0] if idea.targets else idea.entry_price)
            stop_loss = idea.stop_loss
            thesis = idea.reasoning
            risk_factors = idea.risk_factors or ["Earnings miss risk", "Sector rotation reversal", "Macro policy volatility"]

            row = {
                "symbol": symbol,
                "agent_type": "LONGTERM",
                "entry_price": entry_low,
                "stop_loss": stop_loss,
                "targets": idea.targets,
                "confidence_score": idea.confidence_score,
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
        }
        # Archive recommendations no longer in top picks
        if active_symbols:
            try:
                archived = archive_stale_recommendations("LONGTERM", active_symbols)
                if archived:
                    import logging as _log
                    _log.getLogger("LongTermInvestmentAgent").info("Archived %d stale LONGTERM recommendations", archived)
            except Exception:
                pass

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
