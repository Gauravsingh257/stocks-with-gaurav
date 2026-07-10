"""
services/pil/notify.py
======================
Best-effort Telegram delivery for PIL reports/alerts. Mirrors the existing
notification pattern (config.settings bot token/chat id + a direct sendMessage
POST) so PIL reuses the same bot the rest of the platform uses. Never raises —
delivery is fire-and-forget and gated by PIL_TELEGRAM_ENABLED at the caller.
"""

from __future__ import annotations

import logging

log = logging.getLogger("pil.notify")


def send_telegram(message: str) -> bool:
    try:
        import requests
        from config.settings import get_settings
        settings = get_settings()
        token = getattr(settings, "telegram_bot_token", None)
        chat_id = getattr(settings, "telegram_chat_id", None)
        if not token or not chat_id:
            log.debug("[PIL] Telegram not configured — skipping")
            return False
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return bool(resp.ok)
    except Exception as exc:  # pragma: no cover - network/best-effort
        log.warning("[PIL] Telegram send failed: %s", exc)
        return False
