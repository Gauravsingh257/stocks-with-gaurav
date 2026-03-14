from __future__ import annotations

import asyncio

from agents.base import BaseAgent, AgentResult
from dashboard.backend.db import create_stock_recommendation, log_ranking_run
from services import generate_rankings


class SwingTradeAlphaAgent(BaseAgent):
    name = "SwingTradeAlphaAgent"
    description = "Weekly high-conviction swing opportunities (1-8 weeks)."
    schedule = "Weekly scan (Mon 08:30 IST)"
    priority = "high"

    def run(self, result: AgentResult) -> None:
        ranking = asyncio.run(generate_rankings("SWING", top_k=10, target_universe=1800))
        if not ranking.ideas:
            result.status = "WARNING"
            result.summary = "No symbols passed ranking quality gates."
            return

        saved = 0
        findings: list[dict] = []
        for idea in ranking.ideas:
            symbol = idea.symbol
            entry_price = idea.entry_price
            stop_loss = idea.stop_loss
            target_1 = idea.targets[0] if idea.targets else None
            target_2 = idea.targets[1] if len(idea.targets) > 1 else None
            rr = ((target_2 or target_1 or entry_price) - entry_price) / max(entry_price - stop_loss, 0.01)

            row = {
                "symbol": symbol,
                "agent_type": "SWING",
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "targets": idea.targets,
                "confidence_score": idea.confidence_score,
                "setup": idea.setup,
                "expected_holding_period": idea.expected_holding_period,
                "technical_signals": idea.technical_signals,
                "fundamental_signals": idea.fundamental_signals,
                "sentiment_signals": idea.sentiment_signals,
                "technical_factors": idea.technical_factors,
                "fundamental_factors": idea.fundamental_factors,
                "sentiment_factors": idea.sentiment_factors,
                "reasoning": idea.reasoning,
            }
            create_stock_recommendation(row)
            saved += 1
            findings.append(
                {
                    "symbol": symbol,
                    "setup": idea.setup,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "target_1": target_1,
                    "target_2": target_2,
                    "risk_reward": round(rr, 2),
                    "confidence_score": idea.confidence_score,
                    "expected_holding_period": idea.expected_holding_period,
                    "technical_signals": idea.technical_signals,
                    "fundamental_signals": idea.fundamental_signals,
                    "sentiment_signals": idea.sentiment_signals,
                    "technical_factors": idea.technical_factors,
                    "fundamental_factors": idea.fundamental_factors,
                    "sentiment_factors": idea.sentiment_factors,
                    "reasoning_summary": idea.reasoning,
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
        log_ranking_run(
            horizon="SWING",
            universe_requested=ranking.universe.requested_size,
            universe_scanned=ranking.scanned,
            quality_passed=ranking.quality_passed,
            ranked_candidates=ranking.ranked_candidates,
            selected_count=saved,
            notes=f"sources={ranking.universe.sources}",
        )
        result.findings = findings
        result.summary = f"Swing ranking completed. Saved top {saved} names from {ranking.scanned} scanned symbols."
