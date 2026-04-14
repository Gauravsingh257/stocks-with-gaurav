from __future__ import annotations

import asyncio

from agents.base import BaseAgent, AgentResult
from dashboard.backend.db import create_stock_recommendation, log_ranking_run, snapshot_performance, archive_stale_recommendations, expire_old_recommendations
from services import generate_rankings


class SwingTradeAlphaAgent(BaseAgent):
    name = "SwingTradeAlphaAgent"
    description = "Weekly high-conviction swing opportunities (1-8 weeks)."
    schedule = "Weekly scan (Mon 08:30 IST)"
    priority = "high"

    def run(self, result: AgentResult) -> None:
        ranking = asyncio.run(generate_rankings("SWING", top_k=8, target_universe=1800))

        # Log ranking run upfront to get scan_run_id for all recommendations
        run_id = log_ranking_run(
            horizon="SWING",
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
            entry_price = idea.entry_price
            stop_loss = idea.stop_loss
            target_1 = idea.targets[0] if idea.targets else None
            target_2 = idea.targets[1] if len(idea.targets) > 1 else None
            risk = abs(entry_price - stop_loss)
            reward = abs((target_2 or target_1 or entry_price) - entry_price)
            rr = reward / max(risk, 0.01)

            # Determine data authenticity: "real" if setup is SMC-based, else partial/synthetic
            data_auth = "real" if "SMC_SWING" in (idea.setup or "") else "partial"

            # Downgrade confidence for LIMIT entries where CMP is far from entry
            confidence = idea.confidence_score
            if idea.entry_type == "LIMIT" and idea.scan_cmp and entry_price > 0:
                gap_pct = abs(idea.scan_cmp - entry_price) / entry_price * 100
                if gap_pct > 5.0:
                    confidence = round(confidence * 0.7, 2)  # 30% penalty — deep pullback unlikely
                elif gap_pct > 3.0:
                    confidence = round(confidence * 0.85, 2)  # 15% penalty — moderate distance

            row = {
                "symbol": symbol,
                "agent_type": "SWING",
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "targets": idea.targets,
                "confidence_score": confidence,
                "setup": idea.setup,
                "expected_holding_period": idea.expected_holding_period,
                "technical_signals": idea.technical_signals,
                "fundamental_signals": idea.fundamental_signals,
                "sentiment_signals": idea.sentiment_signals,
                "technical_factors": idea.technical_factors,
                "fundamental_factors": idea.fundamental_factors,
                "sentiment_factors": idea.sentiment_factors,
                "reasoning": idea.reasoning,
                "data_authenticity": data_auth,
                "scan_run_id": run_id,
                "entry_type": idea.entry_type,
                "scan_cmp": idea.scan_cmp,
            }
            create_stock_recommendation(row)
            saved += 1
            active_symbols.append(symbol)
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
        # Archive recommendations no longer in top picks
        if active_symbols:
            try:
                archived = archive_stale_recommendations("SWING", active_symbols)
                expired = expire_old_recommendations(max_age_days=7)
                if archived or expired:
                    import logging as _log
                    _log.getLogger("SwingTradeAlphaAgent").info(
                        "Archived %d stale, expired %d old SWING recommendations", archived, expired
                    )
            except Exception:
                pass
        result.findings = findings
        if saved == 0:
            result.status = "WARNING"
            result.summary = (
                f"Swing scan finished: 0 ideas saved (scanned {ranking.scanned} symbols, "
                f"{ranking.quality_passed} quality pass, {ranking.ranked_candidates} ranked). "
                "SMC may have returned SHORT or no signal for the candidate pool; "
                "ensure RESEARCH_SWING_ATR_FALLBACK=1 (default) for ATR when SMC is silent."
            )
            return

        result.summary = f"Swing ranking completed. Saved top {saved} names from {ranking.scanned} scanned symbols."

        # Seed running_trade tracker rows for the new recommendations
        try:
            from services.trade_tracker import seed_running_trades
            seeded = seed_running_trades()
            if seeded:
                import logging as _log
                _log.getLogger("SwingTradeAlphaAgent").info("Trade tracker: seeded %d new rows", seeded)
        except Exception:
            pass

        # Persist daily performance snapshot
        try:
            snapshot_performance()
        except Exception:
            pass
