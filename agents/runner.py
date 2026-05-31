"""
agents/runner.py
APScheduler-based agent runner.

Schedules:
  RiskSentinel      — every 1 min  (09:15–15:30, Mon–Fri)
  TradeManager      — every 5 min  (09:15–15:30, Mon–Fri)
  PreMarketBriefing — 08:45        (Mon–Fri)
  PostMarketAnalyst — 15:30        (Mon–Fri)

Public API (called from FastAPI lifespan):
  start_scheduler()
  stop_scheduler()
  run_agent_now(agent_name) -> dict
  get_agent_statuses() -> list[dict]
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.post_market  import PostMarketAnalyst
from agents.pre_market   import PreMarketBriefing
from agents.trade_manager import TradeManager
from agents.risk_sentinel import RiskSentinel
from agents.oi_intelligence_agent import generate_snapshot as _oi_generate_snapshot
from agents.swing_alpha_agent import SwingTradeAlphaAgent
from agents.longterm_investment_agent import LongTermInvestmentAgent
from agents.reasoning_validator import ReasoningValidatorAgent

logger = logging.getLogger("agents.runner")

# ── Agent registry ───────────────────────────────────────────────────────────
AGENTS = {
    "PostMarketAnalyst": PostMarketAnalyst(),
    "PreMarketBriefing": PreMarketBriefing(),
    "TradeManager":      TradeManager(),
    "RiskSentinel":      RiskSentinel(),
    "SwingTradeAlphaAgent": SwingTradeAlphaAgent(),
    "LongTermInvestmentAgent": LongTermInvestmentAgent(),
    "ReasoningValidatorAgent": ReasoningValidatorAgent(),
}

_scheduler: BackgroundScheduler | None = None


def get_scheduler_running() -> bool:
    """Return True if the APScheduler is currently running."""
    return bool(_scheduler and _scheduler.running)

# ── Market hours guard (IST, UTC+5:30) ───────────────────────────────────────
def _market_hours() -> bool:
    """Return True during 09:15–15:31 IST Mon–Fri."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time
    return time(9, 15) <= t <= time(15, 31)


def _run_agent(agent_name: str) -> None:
    agent = AGENTS.get(agent_name)
    if agent is None:
        logger.error("Unknown agent: %s", agent_name)
        return
    try:
        result = agent.execute()
        level  = "INFO" if result.status == "OK" else "WARNING"
        logger.log(logging.getLevelName(level),
                   "[%s] %s | %s", agent_name, result.status, result.summary)
    except Exception:
        logger.exception("[%s] Unhandled exception", agent_name)


def _run_market_hours(agent_name: str) -> None:
    """Wrapper that skips execution outside market hours."""
    if _market_hours():
        _run_agent(agent_name)


def weekly_swing_scan() -> None:
    """Public wrapper for weekly swing scan."""
    _run_agent("SwingTradeAlphaAgent")


def weekly_longterm_scan() -> None:
    """Public wrapper for weekly long-term ranking scan."""
    _run_agent("LongTermInvestmentAgent")


# ── Lifecycle ────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        logger.info("Scheduler already running")
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    # Pre-market briefing: 08:45 Mon–Fri
    _scheduler.add_job(
        lambda: _run_agent("PreMarketBriefing"),
        CronTrigger(hour=8, minute=45, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="pre_market",
        name="Pre-Market Briefing",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Post-market analyst: 15:30 Mon–Fri
    _scheduler.add_job(
        lambda: _run_agent("PostMarketAnalyst"),
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="post_market",
        name="Post-Market Analyst",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Trade manager: every 5 min (market hours auto-gated inside wrapper)
    _scheduler.add_job(
        lambda: _run_market_hours("TradeManager"),
        CronTrigger(minute="*/5", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="trade_manager",
        name="Trade Manager",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Risk sentinel: every 1 min (market hours gated)
    _scheduler.add_job(
        lambda: _run_market_hours("RiskSentinel"),
        CronTrigger(minute="*", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="risk_sentinel",
        name="Risk Sentinel",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # OI Intelligence: every 60s during market hours (read-only, no AGENTS entry)
    def _run_oi_intelligence():
        if _market_hours():
            try:
                _oi_generate_snapshot()
                logger.debug("[OI Intelligence] snapshot generated")
            except Exception:
                logger.exception("[OI Intelligence] snapshot failed")

    _scheduler.add_job(
        _run_oi_intelligence,
        CronTrigger(minute="*", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="oi_intelligence",
        name="OI Intelligence",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # FVG-Tap: every 5 min during market hours. ISOLATED — flag-gated
    # (FVG_TAP_MODE; off ⟹ instant no-op), best-effort, never touches the
    # live index engine or its Telegram worker. Mirrors the OI pattern.
    def _run_fvg_tap():
        if not _market_hours():
            return
        try:
            from services.fvg_tap_engine import run_fvg_tap_tick
            res = run_fvg_tap_tick()
            status = res.get("status")
            if status not in ("off", "ok"):
                logger.warning("[FVG-Tap] %s", res)
            elif status == "ok":
                # Log every successful tick — evaluated count distinguishes
                # "detector ran, no setups" from a silent yfinance fetch
                # failure (evaluated=0 with no error). Necessary
                # observability for the validation-phase soak.
                logger.info("[FVG-Tap] %s", res)
        except Exception:
            logger.exception("[FVG-Tap] tick failed")

    _scheduler.add_job(
        _run_fvg_tap,
        CronTrigger(minute="*/5", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="fvg_tap",
        name="FVG-Tap (index 5m, flag-gated)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Equity state machine (SWING_SM tracked book): daily pre-market tick.
    # ISOLATED + flag-gated (EQUITY_STATE_MACHINE; off ⟹ instant no-op).
    # This is the persistent ARMED→TAPPED→ENTRY_ACTIVE/EXPIRED tracker that
    # feeds the website "Tracked Setups" section — answers "what happened to
    # yesterday's idea" instead of the daily scan's fresh re-rank. Idempotent
    # per IST day; evaluates the latest CLOSED daily bar. In `alert` mode it
    # publishes recent fires as isolated SWING_SM recommendations (NOT
    # auto-traded, never touches the live index engine). Best-effort.
    def _run_equity_state_machine():
        try:
            import asyncio as _asyncio
            from services.equity_state_machine import run_equity_sm_shadow_tick
            res = _asyncio.run(run_equity_sm_shadow_tick())
            status = res.get("status")
            if status == "disabled":
                return  # flag off — stay silent
            logger.info("[EquitySM] %s", res)
        except Exception:
            logger.exception("[EquitySM] tick failed")

    _scheduler.add_job(
        _run_equity_state_machine,
        CronTrigger(hour=8, minute=35, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="equity_state_machine",
        name="Equity State Machine (SWING_SM tracked book, flag-gated)",
        replace_existing=True,
        misfire_grace_time=600,
        max_instances=1,
    )

    # Kite auto-login: daily 08:50 IST, runs IN THE CLOUD (this web service)
    # so token refresh never depends on the user's PC being on. Previously
    # auto_login.py was scheduled via Windows Task Scheduler on the user's
    # laptop — when the PC was off/asleep at 08:50 the refresh silently
    # missed, leaving the engine with an expired Kite token (root cause of
    # 2026-05-20 "ENGINE STOPPED — TOKEN INVALID"). The existing
    # auto_login() function is idempotent (it checks for an active session
    # first), so running it both here AND from the local task is safe — but
    # ONCE this is confirmed working in cloud, the local Windows Task can be
    # disabled. Required env vars on Railway (web service): KITE_API_KEY,
    # KITE_API_SECRET, KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET. If any
    # are missing, auto_login() exits cleanly; the job logs FAILED and the
    # web service keeps running.
    def _run_kite_auto_login():
        try:
            from auto_login import auto_login as _kite_auto_login
            ok = _kite_auto_login()
            logger.info("[Kite auto-login] %s", "OK" if ok else "FAILED")
        except Exception:
            logger.exception("[Kite auto-login] crashed")

    # KiteTicker WS heartbeat watchdog — every 2 min during market hours.
    # Reads realtime:ws_heartbeat key (written by dashboard/backend/realtime.py
    # on every tick batch, PR #15). If stale (>180s) during market hours →
    # send Telegram alert with 30min cooldown per reason. Catches the silent
    # KiteTicker-death scenario that hit prod 2026-05-25 (macro strip
    # served Friday's data for an entire Monday session).
    _WS_ALERT_COOLDOWN_SEC = 1800
    _ws_alert_last: dict = {}

    def _send_ws_down_alert(reason: str, msg: str) -> None:
        import time as _t
        now = _t.time()
        last = _ws_alert_last.get(reason, 0.0)
        if now - last < _WS_ALERT_COOLDOWN_SEC:
            return
        _ws_alert_last[reason] = now
        try:
            import os as _os, requests as _rq
            bot = _os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            chat = _os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if not bot or not chat:
                return
            _rq.post(
                f"https://api.telegram.org/bot{bot}/sendMessage",
                json={"chat_id": chat,
                      "text": (
                          "🚨 <b>KITETICKER WS DEAD</b>\n"
                          f"<i>Reason:</i> {reason}\n{msg}\n"
                          "Macro strip is serving stale data. The next "
                          "tick should reconnect automatically; if this "
                          "alert repeats, web service restart required."),
                      "parse_mode": "HTML"},
                timeout=8,
            )
        except Exception:
            pass

    def _run_ws_heartbeat_watchdog():
        if not _market_hours():
            return
        try:
            import os as _os, redis as _r, time as _t
            url = _os.getenv("REDIS_URL", "").strip()
            if not url:
                return
            cli = _r.from_url(url, decode_responses=True, socket_timeout=3)
            raw = cli.get("realtime:ws_heartbeat")
            if not raw:
                _send_ws_down_alert(
                    "never-connected",
                    "KiteTicker has not written any heartbeat since boot.")
                return
            try:
                last_ts = int(raw)
            except (TypeError, ValueError):
                return
            age = int(_t.time()) - last_ts
            if age > 180:
                _send_ws_down_alert(
                    f"stale-gt-180s",
                    f"Last KiteTicker tick was {age}s ago (>3 min).")
        except Exception:
            logger.exception("[ws-watchdog] tick failed")

    _scheduler.add_job(
        _run_ws_heartbeat_watchdog,
        CronTrigger(minute="*/2", day_of_week="mon-fri",
                    timezone="Asia/Kolkata"),
        id="ws_heartbeat_watchdog",
        name="KiteTicker WS Heartbeat Watchdog (2m, market-hours)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.add_job(
        _run_kite_auto_login,
        CronTrigger(hour=8, minute=50, day_of_week="mon-fri",
                    timezone="Asia/Kolkata"),
        id="kite_auto_login",
        name="Kite Auto-Login (08:50 IST, cloud)",
        replace_existing=True,
        misfire_grace_time=600,
        max_instances=1,
    )

    # Stage-transition watchdog — every 10 min during market hours.
    # Reads snapshot:last_known_good:discovery (canonical cached scan)
    # and detects symbols that moved UP a bucket since last check:
    #   discovery → watchlist  (approaching entry zone)
    #   watchlist → final      (cleared all 3 gates)
    #   discovery → final      (fast promotion)
    # Demotions are silent. Stores last seen bucket-per-symbol in
    # stage:last_buckets Redis key (24h TTL). 15min cooldown on the
    # overall Telegram digest so user doesn't get spammed if a scan
    # produces many transitions back-to-back.
    _BUCKET_RANK = {"discovery": 1, "watchlist": 2, "final": 3}
    _STAGE_ALERT_COOLDOWN_SEC = 900  # 15 min
    _stage_alert_state: dict = {"last_sent_at": 0.0, "pending": []}

    def _send_stage_alert(promotions: list) -> None:
        """Batched Telegram digest of stage promotions. promotions = list
        of dicts {symbol, from, to, score}. Cooldown enforced."""
        import time as _t
        now = _t.time()
        if not promotions:
            return
        if now - _stage_alert_state["last_sent_at"] < _STAGE_ALERT_COOLDOWN_SEC:
            return
        _stage_alert_state["last_sent_at"] = now
        try:
            import os as _os, requests as _rq
            bot = _os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            chat = _os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if not bot or not chat:
                return
            # Group by destination bucket for readability
            by_dest: dict = {}
            for p in promotions:
                by_dest.setdefault(p["to"], []).append(p)
            lines = ["📈 <b>STAGE PROMOTIONS</b>"]
            for dest in ("final", "watchlist"):
                items = by_dest.get(dest, [])
                if not items:
                    continue
                label = "Final Trades" if dest == "final" else "Approaching Zones"
                lines.append(f"\n<b>→ {label}</b>")
                # Sort by score descending
                items.sort(key=lambda x: x.get("score", 0), reverse=True)
                for it in items[:10]:
                    sym = str(it["symbol"]).replace("NSE:", "")
                    arr = f"({it['from']}→{it['to']})"
                    sc = it.get("score")
                    sc_text = f" · {sc:.0f}%" if isinstance(sc, (int, float)) else ""
                    lines.append(f"  • <b>{sym}</b>{sc_text} {arr}")
                if len(items) > 10:
                    lines.append(f"  …and {len(items)-10} more")
            lines.append(f"\n<i>Review on stockswithgaurav.com/research</i>")
            _rq.post(
                f"https://api.telegram.org/bot{bot}/sendMessage",
                json={"chat_id": chat, "text": "\n".join(lines),
                      "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=8,
            )
        except Exception:
            pass

    def _run_stage_transition_watchdog():
        if not _market_hours():
            return
        try:
            import os as _os, redis as _r, json as _json
            url = _os.getenv("REDIS_URL", "").strip()
            if not url:
                return
            cli = _r.from_url(url, decode_responses=True, socket_timeout=3)
            raw = cli.get("snapshot:last_known_good:discovery")
            if not raw:
                return
            payload = _json.loads(raw)
            # Build current bucket map: symbol → bucket name
            curr_map: dict = {}
            for bucket in ("final", "watchlist", "discovery"):
                # Endpoint uses 'final_trades' for the final bucket
                key = "final_trades" if bucket == "final" else bucket
                for item in payload.get(key, []) or []:
                    sym = item.get("symbol")
                    if sym:
                        curr_map[sym] = {
                            "bucket": bucket,
                            "score": item.get("confidence_score"),
                        }
            if not curr_map:
                return
            # Read previous
            prev_raw = cli.get("stage:last_buckets")
            prev_map: dict = _json.loads(prev_raw) if prev_raw else {}
            # Detect promotions
            promotions = []
            for sym, info in curr_map.items():
                new_bucket = info["bucket"]
                new_rank = _BUCKET_RANK.get(new_bucket, 0)
                old_bucket = (prev_map.get(sym) or {}).get("bucket")
                old_rank = _BUCKET_RANK.get(old_bucket, 0) if old_bucket else 0
                # Promoted = appeared OR moved up a tier
                if old_bucket is None and new_rank >= 2:
                    promotions.append({
                        "symbol": sym, "from": "new", "to": new_bucket,
                        "score": info.get("score"),
                    })
                elif new_rank > old_rank:
                    promotions.append({
                        "symbol": sym, "from": old_bucket or "new",
                        "to": new_bucket, "score": info.get("score"),
                    })
            # Persist current as new baseline (24h TTL)
            cli.set("stage:last_buckets", _json.dumps(curr_map), ex=86400)
            if promotions:
                logger.info("[stage-watchdog] %d promotion(s) detected", len(promotions))
                _send_stage_alert(promotions)
        except Exception:
            logger.exception("[stage-watchdog] tick failed")

    _scheduler.add_job(
        _run_stage_transition_watchdog,
        CronTrigger(minute="*/10", day_of_week="mon-fri",
                    timezone="Asia/Kolkata"),
        id="stage_transition_watchdog",
        name="Discovery Stage-Transition Watchdog (10m, market-hours)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Swing alpha scan: daily before market open (Mon–Fri)
    _scheduler.add_job(
        weekly_swing_scan,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone="Asia/Kolkata"),
        id="daily_swing_scan",
        name="Daily Swing Alpha Scan",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Long-term ranking scan: daily before market open (Mon–Fri)
    _scheduler.add_job(
        weekly_longterm_scan,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=40, timezone="Asia/Kolkata"),
        id="daily_longterm_scan",
        name="Daily Long-Term Ranking Scan",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Reasoning validator: every hour during market hours (Mon–Fri 09:00–16:00)
    _scheduler.add_job(
        lambda: _run_market_hours("ReasoningValidatorAgent"),
        CronTrigger(minute=0, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="hourly_reasoning_validator",
        name="Hourly Reasoning Validator",
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.start()
    logger.info("Agent scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Agent scheduler stopped")
    _scheduler = None


# ── Public helpers ───────────────────────────────────────────────────────────

def run_agent_now(agent_name: str) -> dict:
    """Manually trigger an agent. Returns serialised AgentResult dict."""
    agent = AGENTS.get(agent_name)
    if agent is None:
        return {"error": f"Unknown agent: {agent_name}"}
    try:
        result = agent.execute()
        return result.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def get_agent_statuses() -> list[dict]:
    """Return last-run status + next scheduled run for each agent."""
    from dashboard.backend.db import get_connection

    statuses = []
    for name, agent in AGENTS.items():
        try:
            conn = get_connection()
            row  = conn.execute(
                """
                SELECT run_time, status, summary
                FROM agent_logs
                WHERE agent_name = ?
                ORDER BY run_time DESC
                LIMIT 1
                """,
                (name,),
            ).fetchone()
            conn.close()

            last_run    = dict(row) if row else None
            next_run    = None
            if _scheduler:
                job = _scheduler.get_job(name.lower().replace("analyst","_analyst")
                                                     .replace("briefing","_briefing")
                                                     .replace("manager","_manager")
                                                     .replace("sentinel","_sentinel"))
                # simpler: look up by agent display name
                for j in _scheduler.get_jobs():
                    if name.lower() in j.id:
                        next_run = j.next_run_time.isoformat() if j.next_run_time else None
                        break

            statuses.append({
                "name":        name,
                "description": agent.description,
                "schedule":    agent.schedule,
                "priority":    agent.priority,
                "last_run":    last_run,
                "next_run":    next_run,
            })
        except Exception as e:
            statuses.append({"name": name, "error": str(e)})

    return statuses
