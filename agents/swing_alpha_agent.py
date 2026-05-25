from __future__ import annotations

import asyncio
import os

from agents.base import BaseAgent, AgentResult
from dashboard.backend.db import create_stock_recommendation, log_ranking_run, snapshot_performance, get_stock_recommendations, expire_old_recommendations
from services import generate_rankings

MAX_SWING_SLOTS = int(__import__("os").getenv("MAX_SWING_SLOTS", "25"))
RESEARCH_AGENT_TARGET_UNIVERSE = int(os.getenv("RESEARCH_AGENT_TARGET_UNIVERSE", "2200"))


class SwingTradeAlphaAgent(BaseAgent):
    name = "SwingTradeAlphaAgent"
    description = "Weekly high-conviction swing opportunities (1-8 weeks)."
    schedule = "Weekly scan (Mon 08:30 IST)"
    priority = "high"

    def run(self, result: AgentResult) -> None:
        # The legacy swing scan has early returns (slots full / 0 saved).
        # Two SEPARATE consumers MUST run on every invocation regardless of
        # legacy slot state, so they live in finally{} (genuinely isolated
        # from the legacy slot machine, which previously starved them via
        # those early returns):
        #   _research_feed_tick — refreshes the public Research funnel
        #     (signals_log) + the "X/Y scanned" coverage number (ranking_runs).
        #   _g2_6_tick — the G2-6 planned-execution state machine.
        try:
            self._run_legacy(result)
        finally:
            self._research_feed_tick(result)
            self._g2_6_tick(result)

    def _run_legacy(self, result: AgentResult) -> None:
        # Recycle stale slots BEFORE counting them, so the daily scan can
        # refill freed slots with fresh names. expire_old_recommendations
        # safely skips recs with a RUNNING trade. Env-tunable;
        # RESEARCH_REC_MAX_AGE_DAYS<=0 disables it instantly (live kill
        # switch, no redeploy). Best-effort — never blocks the scan.
        try:
            _max_age = int(os.getenv("RESEARCH_REC_MAX_AGE_DAYS", "7"))
            if _max_age > 0:
                _expired = expire_old_recommendations(max_age_days=_max_age)
                if _expired:
                    result.metrics = {**(result.metrics or {}),
                                      "recommendations_expired": _expired}
        except Exception as exc:
            result.metrics = {**(result.metrics or {}),
                              "rec_expiry_error": str(exc)}

        # Slot-aware scanning: only fill empty slots
        active_recs = get_stock_recommendations("SWING", limit=MAX_SWING_SLOTS)
        active_count = len(active_recs)
        empty_slots = MAX_SWING_SLOTS - active_count
        active_symbols = [r["symbol"] for r in active_recs]

        if empty_slots <= 0:
            result.status = "OK"
            result.summary = f"All {MAX_SWING_SLOTS} swing slots occupied — scan skipped."
            result.metrics = {"active_slots": active_count, "empty_slots": 0}
            return

        # Scan at least 15 candidates even when only a few slots are empty —
        # the extra candidates serve as ranked fallback for the research page.
        scan_top_k = max(empty_slots, int(os.getenv("MIN_SWING_SCAN_K", "15")))
        ranking = asyncio.run(generate_rankings(
            "SWING", top_k=scan_top_k, target_universe=RESEARCH_AGENT_TARGET_UNIVERSE,
            exclude_symbols=active_symbols,
        ))

        # NOTE: the SWING validation scan + signals_log write is no longer
        # done here — it moved to _research_feed_tick (run() finally{}) so it
        # is never starved by the slots-full early return above.

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
            _rec_id = create_stock_recommendation(row)
            # G2-3 SHADOW: canonical FILTERED event (best-effort, never raises).
            try:
                from dashboard.backend.lifecycle_ledger import record_lifecycle_event

                record_lifecycle_event(
                    symbol, "FILTERED", horizon="SWING", source="swing_agent",
                    recommendation_id=_rec_id if isinstance(_rec_id, int) else None,
                    planned_entry=row.get("entry_price"), stop_loss=row.get("stop_loss"),
                    target_1=(row.get("targets") or [None])[0],
                    confidence=row.get("confidence_score"),
                    setup=row.get("setup"), sector=getattr(idea, "sector", None),
                )
            except Exception:
                pass
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
            "active_slots": active_count + saved,
            "empty_slots": empty_slots - saved,
        }
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

        # User-facing Telegram alert on scan completion. Sends ONE
        # batched summary with the top N recommendations rather than
        # per-symbol pings — avoids flooding the channel when a scan
        # produces 10+ ideas at once. Best-effort; never raises.
        try:
            _send_swing_scan_alert(findings, ranking.scanned, horizon="SWING")
        except Exception:
            import logging as _log
            _log.getLogger("SwingTradeAlphaAgent").warning(
                "Swing scan alert: Telegram batch failed (best-effort)")

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

    def _research_feed_tick(self, result: AgentResult) -> None:
        # The public Research funnel (Discovery → Watchlist → Final) and the
        # "Market coverage X/Y scanned" number are fed ONLY by a logged SWING
        # validation scan (signals_log) plus a ranking_runs row. Both writes
        # used to sit BELOW _run_legacy's `if empty_slots <= 0: return` guard,
        # so once the swing slots filled with non-recycling recs the entire
        # funnel AND the coverage number silently froze. This tick runs from
        # run()'s finally{} on EVERY invocation regardless of legacy slot
        # state. Flag-gated by RESEARCH_AGENT_VALIDATION_LOG (byte-identical
        # when off) and fully exception-isolated — it can never break the
        # legacy scan or the live index engine.
        if os.getenv("RESEARCH_AGENT_VALIDATION_LOG", "1").strip().lower() not in (
            "1", "true", "yes",
        ):
            return
        try:
            from services.validation_engine import run_validation_scan

            scan_k = int(os.getenv("RESEARCH_FEED_SCAN_K", "25"))
            validation = asyncio.run(run_validation_scan(
                "SWING",
                top_k=scan_k,
                target_universe=RESEARCH_AGENT_TARGET_UNIVERSE,
                log_scan=True,
            ))
            # Keep the coverage card fresh from the SAME scan — no second pass.
            try:
                log_ranking_run(
                    horizon="SWING",
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

    def _g2_6_tick(self, result: AgentResult) -> None:
        # G2-6 Rung A/B: SEPARATE planned-execution engine. Invoked from
        # run()'s finally{} so it executes on EVERY swing-agent run —
        # independent of the legacy slot machine and its early returns.
        # Gated by EQUITY_STATE_MACHINE:
        #   "shadow" → log would-FIRE to the canonical ledger ONLY.
        #   "alert"  → ALSO publish recent fires as ISOLATED SWING_SM
        #              recommendations (no position, no auto-trade); the
        #              ledger log stays on so scorecard validation runs
        #              in parallel.
        # unset/off ⟹ never entered ⟹ byte-identical. Best-effort: any
        # failure is swallowed and can never affect the swing scan.
        try:
            from services.equity_state_machine import (
                run_equity_sm_shadow_tick, shadow_flag,
            )

            if shadow_flag() in ("shadow", "alert"):
                tick = asyncio.run(run_equity_sm_shadow_tick())
                result.metrics = {**(result.metrics or {}),
                                  "equity_sm_shadow": tick}
        except Exception:
            pass


def _send_swing_scan_alert(findings: list, scanned: int, horizon: str = "SWING") -> None:
    """Telegram batched summary of a scan's new recommendations.

    Added 2026-05-25 after user reported missing alerts. Posts ONE
    message per scan (not one per recommendation) with the top 5 by
    confidence_score. Sends to TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
    (the MTF Alerts channel). Best-effort; never raises into the
    scan loop. If 0 ideas, sends nothing (no-op)."""
    import os
    if not findings:
        return
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    horizon = horizon.upper()
    horizon_emoji = "📈" if horizon == "SWING" else "🌱"
    top_n = sorted(
        findings,
        key=lambda f: float(f.get("confidence_score", 0) or 0),
        reverse=True,
    )[:5]

    lines = [
        f"{horizon_emoji} <b>{horizon} Scan — {len(findings)} new ideas</b>",
        f"<i>Scanned {scanned} symbols. Top {len(top_n)} by confidence:</i>",
        "",
    ]
    for i, f in enumerate(top_n, 1):
        sym = str(f.get("symbol", "?")).replace("NSE:", "")
        entry = f.get("entry_price")
        sl = f.get("stop_loss")
        t1 = f.get("target_1")
        rr = f.get("risk_reward")
        conf = f.get("confidence_score")
        setup = f.get("setup") or ""
        line = f"<b>{i}. {sym}</b>"
        if entry is not None:
            line += f" · ₹{float(entry):.2f}"
        if sl is not None:
            line += f" · SL ₹{float(sl):.2f}"
        if t1 is not None:
            line += f" · T₹{float(t1):.2f}"
        if rr is not None:
            line += f" · RR 1:{rr}"
        if conf is not None:
            line += f" · conf {int(conf)}"
        if setup:
            line += f" · {setup}"
        lines.append(line)
    if len(findings) > len(top_n):
        lines.append("")
        lines.append(f"<i>+ {len(findings) - len(top_n)} more in Research Center → {horizon.capitalize()} tab</i>")

    msg = "\n".join(lines)
    import requests
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception:
        import logging as _log
        _log.getLogger("SwingTradeAlphaAgent").warning(
            "Telegram post failed for scan alert (best-effort)")
