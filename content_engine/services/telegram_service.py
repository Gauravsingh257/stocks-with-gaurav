"""
content_engine/services/telegram_service.py

Standalone Telegram sender for the content engine.
Completely isolated from trade_executor_bot.py and the intraday signal pipeline.

Uses the Bot API directly via httpx — no python-telegram-bot dependency needed.

Public API:
    send_message(settings, text, ...)       → send MarkdownV2 text
    send_plain(settings, text)              → send plain text
    send_image(settings, image_path, ...)   → send photo via sendPhoto
"""

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from content_engine.config.settings import ContentEngineSettings

log = logging.getLogger("content_engine.telegram")

_BASE_URL = "https://api.telegram.org/bot{token}/{method}"


def _api_url(token: str, method: str) -> str:
    return _BASE_URL.format(token=token, method=method)


def send_message(
    settings: ContentEngineSettings,
    text: str,
    parse_mode: str = "MarkdownV2",
    disable_web_page_preview: bool = True,
) -> dict[str, Any]:
    """
    Send a message to TELEGRAM_CONTENT_CHAT_ID.

    Retries up to settings.max_retries times with exponential backoff.
    Raises on final failure so the caller can log/alert.
    """
    url = _api_url(settings.telegram_bot_token, "sendMessage")
    payload = {
        "chat_id": settings.telegram_content_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    last_exc: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            with httpx.Client(timeout=settings.http_timeout_seconds) as client:
                resp = client.post(url, json=payload)
            data = resp.json()

            if not data.get("ok"):
                # Telegram returned an error (e.g. bad parse_mode, chat not found)
                err_desc = data.get("description", "Unknown Telegram error")
                log.error("Telegram API error: %s", err_desc)
                raise ValueError(err_desc)

            log.info(
                "Message sent to chat %s (message_id=%s)",
                settings.telegram_content_chat_id,
                data["result"]["message_id"],
            )
            return data["result"]

        except Exception as exc:
            last_exc = exc
            log.warning(
                "Telegram send attempt %d/%d failed: %s",
                attempt, settings.max_retries, exc,
            )
            if attempt < settings.max_retries:
                time.sleep(settings.retry_backoff_seconds * attempt)

    raise RuntimeError(
        f"Telegram send failed after {settings.max_retries} attempts"
    ) from last_exc


def send_plain(settings: ContentEngineSettings, text: str) -> dict[str, Any]:
    """Send a plain-text message (no Markdown parsing)."""
    return send_message(settings, text, parse_mode="")


def send_image(
    settings: ContentEngineSettings,
    image_path: str,
    caption: str | None = None,
    parse_mode: str = "Markdown",
) -> dict[str, Any]:
    """
    Send a photo to TELEGRAM_CONTENT_CHAT_ID via the sendPhoto API.

    Args:
        settings:   ContentEngineSettings (bot token + chat id)
        image_path: Absolute or relative path to a PNG/JPEG file
        caption:    Optional caption text (supports Markdown by default)
        parse_mode: Parse mode for the caption ("Markdown", "MarkdownV2", or "")

    Returns:
        Telegram API result dict on success.

    Raises:
        FileNotFoundError: if image_path does not exist.
        RuntimeError:      if all retry attempts fail.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    url = _api_url(settings.telegram_bot_token, "sendPhoto")

    last_exc: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            with path.open("rb") as fh:
                files = {"photo": (path.name, fh, "image/png")}
                data: dict[str, Any] = {"chat_id": settings.telegram_content_chat_id}
                if caption:
                    data["caption"] = caption
                    if parse_mode:
                        data["parse_mode"] = parse_mode

                with httpx.Client(timeout=settings.http_timeout_seconds) as client:
                    resp = client.post(url, data=data, files=files)

            result = resp.json()

            if not result.get("ok"):
                err_desc = result.get("description", "Unknown Telegram error")
                log.error("sendPhoto API error: %s", err_desc)
                raise ValueError(err_desc)

            message_id = result["result"]["message_id"]
            log.info(
                "Image sent to chat %s (message_id=%s, file=%s)",
                settings.telegram_content_chat_id,
                message_id,
                path.name,
            )
            return result["result"]

        except Exception as exc:
            last_exc = exc
            log.warning(
                "sendPhoto attempt %d/%d failed for %s: %s",
                attempt, settings.max_retries, path.name, exc,
            )
            if attempt < settings.max_retries:
                time.sleep(settings.retry_backoff_seconds * attempt)

    raise RuntimeError(
        f"sendPhoto failed after {settings.max_retries} attempts for {path.name}"
    ) from last_exc
