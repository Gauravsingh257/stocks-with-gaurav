"""
Content Creation / agents / publisher_agent.py

PublisherAgent -- posts carousel to Instagram via instagrapi (private API).

Flow:
  1. Login to Instagram using username/password (session cached to avoid re-login)
  2. Upload carousel album (multiple images + caption)
  3. Fallback: post to Telegram as media group if Instagram fails

Environment variables:
  INSTAGRAM_USERNAME — Instagram account username
  INSTAGRAM_PASSWORD — Instagram account password
  TELEGRAM_BOT_TOKEN — (optional fallback)
  TELEGRAM_CONTENT_CHAT_ID — (optional fallback)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from agents.base import BaseContentAgent
from models.contracts import CarouselContent, DesignOutput, PublishResult

log = logging.getLogger("content_creation.agents.publisher")

# Session file to avoid re-login each run
_SESSION_FILE = Path(__file__).resolve().parent.parent / "logs" / "ig_session.json"


class PublisherAgent(BaseContentAgent):
    name = "PublisherAgent"
    description = "Posts carousel to Instagram (instagrapi) with Telegram fallback"

    def run(
        self,
        *,
        design: DesignOutput,
        carousel: CarouselContent,
    ) -> PublishResult:
        ig_user = getattr(self.settings, "instagram_username", "")
        ig_pass = getattr(self.settings, "instagram_password", "")

        # ── Try Instagram via instagrapi ──────────────────────────────────
        if ig_user and ig_pass:
            try:
                result = self._publish_instagram(
                    design, carousel, ig_user, ig_pass
                )
                if result.success:
                    log.info("Published to Instagram: %s", result.post_id)
                    return result
            except Exception as e:
                log.error("Instagram publish failed: %s", e)

        # ── Fallback to Telegram ──────────────────────────────────────────
        tg_token = getattr(self.settings, "telegram_bot_token", "")
        tg_chat = getattr(self.settings, "telegram_content_chat_id", "")

        if tg_token and tg_chat:
            try:
                result = self._publish_telegram(
                    design, carousel, tg_token, tg_chat
                )
                if result.success:
                    log.info("Published to Telegram as fallback")
                    return result
            except Exception as e:
                log.error("Telegram publish failed: %s", e)

        # ── No channel configured ─────────────────────────────────────────
        log.warning("No publishing channel configured -- skipping publish")
        return PublishResult(
            success=False,
            error="No publishing channels configured (set INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD or TELEGRAM_BOT_TOKEN)",
        )

    # ── Instagram via instagrapi ──────────────────────────────────────────

    def _publish_instagram(
        self,
        design: DesignOutput,
        carousel: CarouselContent,
        username: str,
        password: str,
    ) -> PublishResult:
        """Upload carousel album using instagrapi private API."""
        from instagrapi import Client
        from instagrapi.exceptions import LoginRequired

        cl = Client()

        # Try loading cached session first to avoid rate-limit on login
        session_loaded = False
        if _SESSION_FILE.exists():
            try:
                cl.load_settings(_SESSION_FILE)
                cl.login(username, password)
                cl.get_timeline_feed()  # lightweight check that session is alive
                session_loaded = True
                log.info("Instagram session restored from cache")
            except LoginRequired:
                log.info("Cached session expired, performing fresh login")
                cl = Client()
            except Exception as e:
                log.warning("Session restore failed: %s -- doing fresh login", e)
                cl = Client()

        if not session_loaded:
            cl.login(username, password)
            _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            cl.dump_settings(_SESSION_FILE)
            log.info("Fresh Instagram login, session saved")

        # Collect valid image paths
        image_paths = [
            Path(p) for p in design.slide_paths if Path(p).exists()
        ]
        if not image_paths:
            return PublishResult(success=False, error="No valid images to upload")

        # Build caption
        caption = carousel.caption + "\n\n" + " ".join(carousel.hashtags)
        caption = caption[:2200]  # Instagram caption limit

        # Upload carousel album
        if len(image_paths) == 1:
            media = cl.photo_upload(image_paths[0], caption=caption)
        else:
            media = cl.album_upload(image_paths, caption=caption)

        post_id = media.pk if media else ""
        post_code = media.code if media else ""
        post_url = f"https://www.instagram.com/p/{post_code}/" if post_code else ""

        return PublishResult(
            success=True,
            platform="instagram",
            post_id=str(post_id),
            post_url=post_url,
            published_at=datetime.now(),
        )

    # ── Telegram Fallback Publishing ──────────────────────────────────────

    def _publish_telegram(
        self,
        design: DesignOutput,
        carousel: CarouselContent,
        bot_token: str,
        chat_id: str,
    ) -> PublishResult:
        """Send carousel as a media group to Telegram."""
        caption = carousel.caption + "\n\n" + " ".join(carousel.hashtags[:10])

        media_group = []
        files = {}
        for i, img_path in enumerate(design.slide_paths):
            p = Path(img_path)
            if not p.exists():
                continue
            file_key = f"photo_{i}"
            files[file_key] = (p.name, p.read_bytes(), "image/png")
            item = {"type": "photo", "media": f"attach://{file_key}"}
            if i == 0:
                item["caption"] = caption[:1024]
                item["parse_mode"] = "HTML"
            media_group.append(item)

        if not media_group:
            return PublishResult(success=False, error="No valid images for Telegram")

        tg_url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                tg_url,
                data={"chat_id": chat_id, "media": json.dumps(media_group)},
                files=files,
            )

        if resp.status_code == 200:
            result_data = resp.json().get("result", [{}])
            msg_id = str(result_data[0].get("message_id", "")) if result_data else ""
            return PublishResult(
                success=True,
                platform="telegram",
                post_id=msg_id,
                published_at=datetime.now(),
            )

        return PublishResult(
            success=False,
            platform="telegram",
            error=f"Telegram API error: {resp.status_code}",
        )
