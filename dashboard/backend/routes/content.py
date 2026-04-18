"""
dashboard/backend/routes/content.py
Content calendar & Make.com webhook endpoints.

Provides:
- POST /api/content/webhook/notion-to-post   — Make.com calls this when Notion status → "✂️ Edited"
- POST /api/content/push-signal              — Engine pushes signal → Notion content idea
- POST /api/content/push-trade               — Engine pushes closed trade → Notion content idea
- GET  /api/content/upcoming                 — Get upcoming week's content
- POST /api/content/setup                    — Create Notion database (one-time setup)
- POST /api/content/seed-week                — Seed a week's content from template
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/content", tags=["content"])
_IST = timezone(timedelta(hours=5, minutes=30))


# ── Models ───────────────────────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    """Payload sent by Make.com when Notion entry status changes."""
    notion_page_id: str
    title: str
    status: str
    content_type: str = ""
    platforms: list[str] = Field(default_factory=list)
    video_url: Optional[str] = None
    caption: str = ""
    hashtags: str = ""
    scheduled_date: Optional[str] = None


class SignalPushRequest(BaseModel):
    """Push an engine signal to Notion."""
    symbol: str
    setup_type: str = "order_block"
    bias: str = "BULLISH"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    targets: list[float] = Field(default_factory=list)
    timeframe: str = "5m"


class TradeResultRequest(BaseModel):
    """Push a closed trade result to Notion."""
    symbol: str
    setup_type: str = "order_block"
    entry_price: float
    exit_price: float
    pnl: float
    pnl_r: float = 0.0
    result: str = "WIN"


class SetupRequest(BaseModel):
    """One-time database setup."""
    notion_api_key: str
    parent_page_id: str


class SeedWeekRequest(BaseModel):
    """Seed a week's content."""
    week_start_date: str  # YYYY-MM-DD (Monday)
    tuesday_topic: str = "Smart Money Concepts"
    wednesday_setup: str = "SMC Entry Model"
    thursday_day_n: str = "X"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_notion_client():
    """Get Notion client from env vars."""
    api_key = os.getenv("NOTION_API_KEY", "")
    page_id = os.getenv("NOTION_PARENT_PAGE_ID", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="NOTION_API_KEY not configured")
    from services.notion_content.client import NotionContentClient
    return NotionContentClient(api_key=api_key, parent_page_id=page_id)


def _get_database_id() -> str:
    db_id = os.getenv("NOTION_CONTENT_DB_ID", "")
    if not db_id:
        raise HTTPException(status_code=500, detail="NOTION_CONTENT_DB_ID not configured. Run /api/content/setup first.")
    return db_id


def _verify_webhook_secret(request_body: bytes, signature: str | None) -> bool:
    """Verify Make.com webhook signature if MAKE_WEBHOOK_SECRET is set."""
    secret = os.getenv("MAKE_WEBHOOK_SECRET", "")
    if not secret:
        return True  # No secret configured = skip verification
    if not signature:
        return False
    expected = hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/setup")
async def setup_notion_database(req: SetupRequest):
    """
    One-time: Create the Content Calendar database in Notion.
    Save the returned database_id as NOTION_CONTENT_DB_ID env var.
    """
    from services.notion_content.client import NotionContentClient
    client = NotionContentClient(api_key=req.notion_api_key, parent_page_id=req.parent_page_id)
    try:
        db_id = client.create_content_calendar()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create Notion DB: {e}")

    return {
        "status": "ok",
        "database_id": db_id,
        "next_step": "Add NOTION_CONTENT_DB_ID={} to your .env file".format(db_id),
    }


@router.post("/seed-week")
async def seed_week(req: SeedWeekRequest):
    """Seed a week's content from the template."""
    client = _get_notion_client()
    db_id = _get_database_id()
    from services.notion_content.client import seed_weekly_content
    try:
        page_ids = seed_weekly_content(
            client, db_id, req.week_start_date,
            topics={
                "Tuesday_topic": req.tuesday_topic,
                "Wednesday_setup": req.wednesday_setup,
                "Thursday_day_n": req.thursday_day_n,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "entries_created": len(page_ids), "page_ids": page_ids}


@router.post("/push-signal")
async def push_signal(req: SignalPushRequest):
    """Push an engine signal to Notion as a content idea."""
    client = _get_notion_client()
    db_id = _get_database_id()
    from services.notion_content.signal_bridge import push_signal_to_notion
    try:
        page_id = push_signal_to_notion(client, db_id, req.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "notion_page_id": page_id}


@router.post("/push-trade")
async def push_trade(req: TradeResultRequest):
    """Push a closed trade result to Notion as content idea."""
    client = _get_notion_client()
    db_id = _get_database_id()
    from services.notion_content.signal_bridge import push_trade_result_to_notion
    try:
        page_id = push_trade_result_to_notion(client, db_id, req.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "notion_page_id": page_id}


@router.get("/upcoming")
async def get_upcoming(days: int = 7):
    """Get upcoming content for the next N days."""
    client = _get_notion_client()
    db_id = _get_database_id()
    try:
        entries = client.get_upcoming_content(db_id, days_ahead=days)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = []
    for entry in entries:
        props = entry.get("properties", {})
        results.append({
            "id": entry["id"],
            "title": _extract_title(props),
            "status": props.get("Status", {}).get("select", {}).get("name", ""),
            "scheduled_date": props.get("Scheduled Date", {}).get("date", {}).get("start", ""),
            "content_type": props.get("Content Type", {}).get("select", {}).get("name", ""),
            "platforms": [p["name"] for p in props.get("Platforms", {}).get("multi_select", [])],
        })
    return {"upcoming": results, "count": len(results)}


@router.post("/webhook/notion-to-post")
async def webhook_notion_to_post(
    request: Request,
    x_make_signature: Optional[str] = Header(None),
):
    """
    Make.com webhook: triggered when Notion entry status → "✂️ Edited".
    
    This endpoint receives the content details and can:
    1. Log it for manual posting via Meta Business Suite
    2. Send a Telegram notification with posting reminder
    3. (Future) Auto-post via platform APIs
    """
    body = await request.body()
    if not _verify_webhook_secret(body, x_make_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = WebhookPayload.model_validate_json(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    logger.info(f"[Make.com webhook] Content ready: {payload.title} → {payload.platforms}")

    # Send Telegram notification for manual posting
    notification = (
        f"📹 *Content Ready to Post*\n\n"
        f"*{payload.title}*\n"
        f"Type: {payload.content_type}\n"
        f"Platforms: {', '.join(payload.platforms)}\n"
    )
    if payload.video_url:
        notification += f"Video: {payload.video_url}\n"
    if payload.scheduled_date:
        notification += f"Scheduled: {payload.scheduled_date}\n"

    try:
        from services.telegram_bot import send_telegram_message
        send_telegram_message(notification, parse_mode="Markdown")
        logger.info(f"Telegram notification sent for: {payload.title}")
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")

    # Update Notion status to "📅 Scheduled"
    try:
        client = _get_notion_client()
        client.update_status(payload.notion_page_id, "📅 Scheduled")
    except Exception as e:
        logger.warning(f"Notion status update failed: {e}")

    return {
        "status": "ok",
        "message": f"Content '{payload.title}' queued for posting",
        "telegram_notified": True,
        "platforms": payload.platforms,
    }


# ── Utils ────────────────────────────────────────────────────────────────────

def _extract_title(props: dict) -> str:
    title_prop = props.get("Title", {}).get("title", [])
    if title_prop:
        return title_prop[0].get("text", {}).get("content", "")
    return ""
