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

    # ── Assemble the system prompt ────────────────────────────────────────────
    today_str = datetime.now().strftime("%A, %d %B %Y %H:%M IST")
    paper_str = " (PAPER MODE)" if paper_mode else ""

    prompt = f"""You are an expert SMC (Smart Money Concepts) trading assistant for an Indian index options trader.
You have LIVE access to the trader's engine state and trade journal. Today is {today_str}.

=== ENGINE STATUS{paper_str} ===
Mode: {engine_mode}
Market Regime: {regime}
Circuit Breaker: {"⛔ ACTIVE — no new trades" if cb_active else "✅ OFF"}
Daily P&L: {daily_pnl:+.2f}R
Consecutive Losses: {consec_losses}
Signals Today: {signals_today}/{max_signals}
Index Only: {"Yes" if index_only else "No"}
Engine Live: {"Yes (real-time)" if engine_live else "No (standalone)"}

=== ACTIVE TRADES ({len(active_trades)}) ===
{active_trades_text}

=== TODAY — {datetime.now().date().isoformat()} ===
Trades: {today_total} | Wins: {today_wins} ({today_wr}) | P&L: {today_pnl:+.2f}R
Recent trades:{today_trade_text}

=== ROLLING 20-TRADE STATS ===
Total: {r20_total} | Wins: {r20_wins} | WR: {r20_wr:.1f}% | PF: {r20_pf:.2f} | Total R: {r20_pnl:+.2f}R

=== RECENT AGENT RUNS ===
{agent_summary}

=== YOUR ROLE ===
1. Answer questions about the trader's live P&L, positions, regime, risk, and performance.
2. Compute derived stats on request (expectancy, drawdown, best setup, etc.) using the data above.
3. Offer actionable, context-aware insights — e.g. warn if CB is close, suggest regime-appropriate setups.
4. Be concise and precise. Use R-multiples. Prefer tables or bullet lists for data.
5. If asked about something outside your context (e.g. macro news, stock prices), say so clearly.
6. Never advise taking a specific trade you haven't been asked to evaluate.
7. When showing numbers, format them clearly: +2.40R, 42.8% WR, PF 1.83, etc.
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
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        # Truncate to last 20 messages to stay within GPT-4o context budget
        MAX_HISTORY = 20
        truncated = messages[-MAX_HISTORY:] if len(messages) > MAX_HISTORY else messages

        oai_messages = [{"role": "system", "content": system_prompt}]
        for m in truncated:
            oai_messages.append({"role": m.role, "content": m.content})

        with client.chat.completions.create(
            model="gpt-3.5-turbo",
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
