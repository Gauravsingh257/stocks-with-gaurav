"""
dashboard/backend/routes/chat.py
GPT-4o powered trading assistant.

POST /api/chat          — streaming SSE chat with full engine context
GET  /api/chat/context  — preview the system prompt context (debug)

OPENAI_API_KEY must be set as an environment variable.
The system prompt is rebuilt on every request so the AI always sees live data.
"""

import json
import logging
import os
from datetime import datetime
from typing import Generator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("dashboard.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Singleton OpenAI client (reuses HTTP connection pool) ─────────────────────
_openai_client = None
_openai_client_key = None


def _get_openai_client(api_key: str):
    global _openai_client, _openai_client_key
    if _openai_client is None or _openai_client_key != api_key:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=api_key)
        _openai_client_key = api_key
    return _openai_client


# ── Request / response schemas ────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str     # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]


# ── System prompt builder ─────────────────────────────────────────────────────
def _build_system_prompt() -> tuple[str, dict]:
    """
    Construct a rich system prompt from live engine state.
    Returns (prompt_text, context_dict) — context_dict for the /context debug endpoint.
    """
    ctx: dict = {}

    # ── Engine snapshot ──────────────────────────────────────────────────────
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap = get_engine_snapshot()

        daily_pnl     = snap.get("daily_pnl_r", 0)
        cb_active     = snap.get("circuit_breaker_active", False)
        regime        = snap.get("market_regime", "NEUTRAL")
        engine_mode   = snap.get("engine_mode", "UNKNOWN")
        consec_losses = snap.get("consecutive_losses", 0)
        signals_today = snap.get("signals_today", 0)
        max_signals   = snap.get("max_daily_signals", 5)
        paper_mode    = snap.get("paper_mode", False)
        index_only    = snap.get("index_only", True)
        active_trades = snap.get("active_trades", [])
        engine_live   = snap.get("engine_live", False)

        ctx["engine_snapshot"] = {
            "daily_pnl_r":       daily_pnl,
            "circuit_breaker":   cb_active,
            "market_regime":     regime,
            "engine_mode":       engine_mode,
            "consecutive_losses": consec_losses,
            "signals_today":     signals_today,
            "max_daily_signals": max_signals,
            "paper_mode":        paper_mode,
            "index_only":        index_only,
            "active_trade_count": len(active_trades),
        }

        # Active trades summary
        trade_lines = []
        for t in active_trades:
            sym = str(t.get("symbol", "?")).replace("NSE:", "")
            dirn = t.get("direction", "?")
            entry = t.get("entry", "?")
            sl = t.get("sl") or t.get("stop_loss", "?")
            tp = t.get("target") or t.get("tp", "?")
            rr = t.get("rr", "?")
            trade_lines.append(f"  - {sym} {dirn} @ {entry} | SL: {sl} | Target: {tp} | RR: {rr}R")

        active_trades_text = "\n".join(trade_lines) if trade_lines else "  None"

    except Exception as e:
        logger.warning("[Chat] Engine snapshot failed: %s", e)
        daily_pnl = cb_active = 0
        regime = "NEUTRAL"
        engine_mode = "STANDALONE"
        consec_losses = signals_today = 0
        max_signals = 5
        paper_mode = index_only = engine_live = False
        active_trades = []
        active_trades_text = "  Unknown (engine offline)"

    # ── Today's closed trades ────────────────────────────────────────────────
    try:
        from dashboard.backend.db import get_connection
        today = datetime.now().date().isoformat()
        conn  = get_connection()
        today_rows = conn.execute(
            "SELECT symbol, direction, setup, entry, exit_price, result, pnl_r FROM trades WHERE date(date) = ?",
            (today,),
        ).fetchall()

        # Rolling 20 stats
        last20 = conn.execute(
            "SELECT result, pnl_r, setup FROM trades WHERE result IN ('WIN','LOSS') ORDER BY date DESC LIMIT 20"
        ).fetchall()
        conn.close()

        today_total = len(today_rows)
        today_wins  = sum(1 for r in today_rows if r["result"] == "WIN")
        today_pnl   = sum((r["pnl_r"] or 0) for r in today_rows)

        # Build a brief today trade list (last 5)
        today_trade_text = ""
        for r in list(today_rows)[-5:]:
            sym   = str(r["symbol"]).replace("NSE:", "")
            sign  = "+" if (r["pnl_r"] or 0) >= 0 else ""
            today_trade_text += f"\n  - {sym} {r['direction']} {r['setup']} → {r['result']} ({sign}{r['pnl_r']:.2f}R)"
        if not today_trade_text:
            today_trade_text = "\n  None yet today"

        # Rolling 20 stats
        r20_total  = len(last20)
        r20_wins   = sum(1 for r in last20 if r["result"] == "WIN")
        r20_pnl    = sum((r["pnl_r"] or 0) for r in last20)
        r20_wr     = (r20_wins / r20_total * 100) if r20_total else 0
        gross_win  = sum((r["pnl_r"] or 0) for r in last20 if (r["pnl_r"] or 0) > 0)
        gross_loss = abs(sum((r["pnl_r"] or 0) for r in last20 if (r["pnl_r"] or 0) < 0))
        r20_pf     = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

        ctx["today"] = {
            "trades": today_total,
            "wins":   today_wins,
            "pnl_r":  round(today_pnl, 3),
        }
        ctx["rolling_20"] = {
            "total":           r20_total,
            "win_rate_pct":    round(r20_wr, 1),
            "profit_factor":   round(r20_pf, 2) if r20_pf != float("inf") else 999,
            "total_r":         round(r20_pnl, 3),
        }

        today_wr = f"{today_wins/today_total*100:.0f}%" if today_total else "N/A"

    except Exception as e:
        logger.warning("[Chat] Trade DB query failed: %s", e)
        today_total = today_wins = 0
        today_pnl   = 0.0
        today_wr    = "N/A"
        today_trade_text = "\n  No trade data available"
        r20_wr = r20_pf = r20_pnl = r20_total = 0
        r20_wins = 0

    # ── Recent agent runs ────────────────────────────────────────────────────
    agent_summary = ""
    try:
        from dashboard.backend.db import get_connection as _gc
        conn2 = _gc()
        agent_rows = conn2.execute(
            "SELECT agent_name, run_time, status, summary FROM agent_logs ORDER BY run_time DESC LIMIT 5"
        ).fetchall()
        conn2.close()
        for row in agent_rows:
            agent_summary += f"\n  [{row['agent_name']}] {row['run_time'][:16]} → {row['status']}: {row['summary'][:120]}"
        if not agent_summary:
            agent_summary = "\n  No agent runs yet"
        ctx["recent_agents"] = [dict(r) for r in agent_rows]
    except Exception:
        agent_summary = "\n  Agent data unavailable"

    # ── All-time intraday stats ───────────────────────────────────────────────
    intraday_all_text = ""
    try:
        from dashboard.backend.db import get_connection as _gc2
        conn3 = _gc2()
        all_trades = conn3.execute(
            "SELECT result, pnl_r, setup FROM trades WHERE result IN ('WIN','LOSS')"
        ).fetchall()
        conn3.close()
        if all_trades:
            aw = [r for r in all_trades if r["result"] == "WIN"]
            al = [r for r in all_trades if r["result"] == "LOSS"]
            a_total = len(all_trades)
            a_wr    = round(len(aw) / a_total * 100, 1)
            a_r     = round(sum(r["pnl_r"] or 0 for r in all_trades), 2)
            a_gp    = sum(r["pnl_r"] or 0 for r in aw)
            a_gl    = abs(sum(r["pnl_r"] or 0 for r in al))
            a_pf    = round(a_gp / a_gl, 2) if a_gl > 0 else 0
            a_exp   = round(a_r / a_total, 3)
            # Best/worst setup
            setup_map: dict = {}
            for r in all_trades:
                s = (r["setup"] or "Unknown").strip() or "Unknown"
                setup_map.setdefault(s, {"wins": 0, "total": 0, "r": 0})
                setup_map[s]["total"] += 1
                setup_map[s]["r"] += r["pnl_r"] or 0
                if r["result"] == "WIN":
                    setup_map[s]["wins"] += 1
            best_setup  = max(setup_map, key=lambda s: setup_map[s]["r"]) if setup_map else "N/A"
            worst_setup = min(setup_map, key=lambda s: setup_map[s]["r"]) if setup_map else "N/A"
            intraday_all_text = (
                f"Total: {a_total} | WR: {a_wr}% | PF: {a_pf} | "
                f"Total R: {a_r:+.2f} | Expectancy: {a_exp:+.3f}R/trade\n"
                f"Best setup: {best_setup} ({setup_map.get(best_setup,{}).get('r',0):+.2f}R) | "
                f"Worst setup: {worst_setup} ({setup_map.get(worst_setup,{}).get('r',0):+.2f}R)"
            )
            ctx["intraday_all_time"] = {
                "total": a_total, "win_rate_pct": a_wr, "profit_factor": a_pf,
                "total_r": a_r, "expectancy": a_exp,
                "best_setup": best_setup, "worst_setup": worst_setup,
            }
        else:
            intraday_all_text = "  No closed intraday trades yet"
    except Exception as e:
        logger.warning("[Chat] All-time intraday stats failed: %s", e)
        intraday_all_text = "  Data unavailable"

    # ── Swing & Long-term research performance ───────────────────────────────
    research_text = ""
    try:
        from dashboard.backend.db import get_connection as _gc3
        conn4 = _gc3()
        rt_all = conn4.execute(
            """SELECT sr.agent_type, sr.symbol, rt.status, rt.profit_loss_pct, rt.days_held
               FROM running_trades rt
               JOIN stock_recommendations sr ON rt.recommendation_id = sr.id
               WHERE sr.agent_type IN ('SWING','LONGTERM')""",
        ).fetchall()
        # Last 5 swing + 3 LT recommendations
        last_swing_recs = conn4.execute(
            """SELECT sr.symbol, sr.entry_price, sr.setup, sr.created_at,
                      rt.current_price, rt.profit_loss_pct, rt.status
               FROM stock_recommendations sr
               LEFT JOIN running_trades rt
                   ON rt.recommendation_id = sr.id
                   AND rt.id = (SELECT MAX(id) FROM running_trades WHERE recommendation_id = sr.id)
               WHERE sr.agent_type = 'SWING'
               ORDER BY sr.created_at DESC LIMIT 5""",
        ).fetchall()
        last_lt_recs = conn4.execute(
            """SELECT sr.symbol, sr.entry_price, sr.setup, sr.created_at,
                      rt.current_price, rt.profit_loss_pct, rt.status
               FROM stock_recommendations sr
               LEFT JOIN running_trades rt
                   ON rt.recommendation_id = sr.id
                   AND rt.id = (SELECT MAX(id) FROM running_trades WHERE recommendation_id = sr.id)
               WHERE sr.agent_type = 'LONGTERM'
               ORDER BY sr.created_at DESC LIMIT 3""",
        ).fetchall()
        conn4.close()

        def _research_block(horizon: str, rows) -> str:
            subset = [r for r in rows if r["agent_type"] == horizon]
            if not subset:
                return f"No {horizon} data yet."
            closed   = [r for r in subset if r["status"] in ("TARGET_HIT", "STOP_HIT")]
            hits     = [r for r in subset if r["status"] == "TARGET_HIT"]
            active   = [r for r in subset if r["status"] == "RUNNING"]
            hit_rate = round(len(hits) / len(closed) * 100, 1) if closed else 0
            avg_pnl  = round(sum(r["profit_loss_pct"] for r in subset) / len(subset), 2)
            best = max(subset, key=lambda r: r["profit_loss_pct"])
            worst = min(subset, key=lambda r: r["profit_loss_pct"])
            return (
                f"Total: {len(subset)} | Active: {len(active)} | Hit Rate: {hit_rate}%\n"
                f"Avg P&L: {avg_pnl:+.1f}% | Best: {best['symbol']} ({best['profit_loss_pct']:+.1f}%) "
                f"| Worst: {worst['symbol']} ({worst['profit_loss_pct']:+.1f}%)"
            )

        swing_perf_text = _research_block("SWING", rt_all)
        lt_perf_text    = _research_block("LONGTERM", rt_all)

        def _rec_line(r) -> str:
            pnl = r["profit_loss_pct"] or 0
            cmp = r["current_price"] or r["entry_price"]
            sign = "+" if pnl >= 0 else ""
            status = r["status"] or "PENDING"
            return f"  {r['symbol']} | Entry ₹{r['entry_price']} | CMP ₹{cmp} | P&L {sign}{pnl:.1f}% | {status}"

        swing_recs_text = "\n".join(_rec_line(r) for r in last_swing_recs) or "  None"
        lt_recs_text    = "\n".join(_rec_line(r) for r in last_lt_recs) or "  None"

        research_text = (
            f"SWING PERFORMANCE:\n{swing_perf_text}\n"
            f"LONGTERM PERFORMANCE:\n{lt_perf_text}"
        )
        ctx["research_performance"] = {
            "swing": swing_perf_text,
            "longterm": lt_perf_text,
            "last_5_swing": [dict(r) for r in last_swing_recs],
            "last_3_lt": [dict(r) for r in last_lt_recs],
        }
    except Exception as e:
        logger.warning("[Chat] Research performance context failed: %s", e)
        swing_recs_text = lt_recs_text = research_text = "  Data unavailable"

    # ── Assemble the system prompt ────────────────────────────────────────────
    today_str = datetime.now().strftime("%A, %d %B %Y %H:%M IST")
    paper_str = " (PAPER MODE)" if paper_mode else ""

    prompt = f"""You are an expert SMC (Smart Money Concepts) trading assistant for an Indian equity/index options trader.
You have LIVE access to the trader's engine state, intraday trade journal, swing picks, and long-term ideas. Today is {today_str}.

=== ENGINE STATUS{paper_str} ===
Mode: {engine_mode}
Market Regime: {regime}
Circuit Breaker: {"⛔ ACTIVE — no new trades" if cb_active else "✅ OFF"}
Daily P&L: {daily_pnl:+.2f}R
Consecutive Losses: {consec_losses}
Signals Today: {signals_today}/{max_signals}
Index Only: {"Yes" if index_only else "No"}
Engine Live: {"Yes (real-time)" if engine_live else "No (standalone)"}

=== ACTIVE INTRADAY TRADES ({len(active_trades)}) ===
{active_trades_text}

=== TODAY — {datetime.now().date().isoformat()} ===
Trades: {today_total} | Wins: {today_wins} ({today_wr}) | P&L: {today_pnl:+.2f}R
Recent trades:{today_trade_text}

=== ROLLING 20-TRADE STATS ===
Total: {r20_total} | Wins: {r20_wins} | WR: {r20_wr:.1f}% | PF: {r20_pf:.2f} | Total R: {r20_pnl:+.2f}R

=== ALL-TIME INTRADAY STATS ===
{intraday_all_text}

=== SWING & LONG-TERM RESEARCH PERFORMANCE ===
{research_text}

=== LAST 5 SWING RECOMMENDATIONS ===
{swing_recs_text}

=== LAST 3 LONG-TERM RECOMMENDATIONS ===
{lt_recs_text}

=== RECENT AGENT RUNS ===
{agent_summary}

=== YOUR ROLE ===
1. Answer questions about live P&L, positions, regime, risk, and performance across ALL horizons (intraday, swing, long-term).
2. Compute derived stats on request (expectancy, drawdown, hit rate, best/worst pick, etc.) using the data above.
3. Offer actionable, context-aware insights — e.g. warn if CB is close, highlight the best-performing setup or stock pick.
4. Be concise and precise. Use R-multiples for intraday, P&L% for swing/LT. Prefer tables or bullet lists.
5. If asked about something outside your context (e.g. live prices, macro news), say so clearly.
6. Never advise taking a specific trade you haven't been asked to evaluate.
7. When showing numbers, format clearly: +2.40R, 42.8% WR, PF 1.83, +12.4% gain, etc.
"""

    return prompt, ctx


# ── SSE streaming helper ──────────────────────────────────────────────────────
def _sse_chunk(data: str) -> str:
    """Format a Server-Sent Events data line."""
    return f"data: {json.dumps({'delta': data})}\n\n"

def _sse_done() -> str:
    return "data: [DONE]\n\n"

def _sse_error(msg: str) -> str:
    return f"data: {json.dumps({'error': msg})}\n\n"


# ── Stream generator ──────────────────────────────────────────────────────────
def _stream_chat(messages: list[ChatMessage], system_prompt: str) -> Generator[str, None, None]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        yield _sse_error("OPENAI_API_KEY is not set. Add it to your environment variables.")
        yield _sse_done()
        return

    try:
        client = _get_openai_client(api_key)

        # Truncate to last 20 messages to stay within GPT-4o context budget
        MAX_HISTORY = 20
        truncated = messages[-MAX_HISTORY:] if len(messages) > MAX_HISTORY else messages

        oai_messages = [{"role": "system", "content": system_prompt}]
        for m in truncated:
            oai_messages.append({"role": m.role, "content": m.content})

        with client.chat.completions.create(
            model="gpt-4o-mini",
            messages=oai_messages,
            max_tokens=1024,
            temperature=0.3,
            stream=True,
        ) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield _sse_chunk(delta)

        yield _sse_done()

    except Exception as exc:
        err = str(exc)
        logger.error("[Chat] OpenAI error: %s", err)
        yield _sse_error(f"OpenAI error: {err}")
        yield _sse_done()


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("")
async def chat(body: ChatRequest):
    """
    Streaming chat endpoint.
    Returns SSE stream: data: {"delta": "text"} … data: [DONE]
    """
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages array is required")

    system_prompt, _ = _build_system_prompt()

    return StreamingResponse(
        _stream_chat(body.messages, system_prompt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/context")
def chat_context():
    """Return the context dict that would be injected into the system prompt (debug/preview)."""
    _, ctx = _build_system_prompt()
    return ctx
