"""
Auto Content Pipeline — Generate carousel + send to Telegram channel.

Usage:
    python scripts/auto_content_post.py pre     # Pre-market (8:30 AM)
    python scripts/auto_content_post.py post    # Post-market (4:20 PM)

Flow:
    1. Run Content Creation/main.py --now {mode} --dry-run
    2. Collect all PNG slides + TXT copy kit from output folder
    3. Send as media group to SWG Content Drafts Telegram channel
    4. Send copy kit as a separate text message
"""

import os
import sys
import glob
import json
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# ── Setup ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent  # C:\Users\g6666\Trading Algo
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("auto_content")

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CONTENT_CHAT_ID = os.getenv("TELEGRAM_CONTENT_CHAT_ID", "-1003831117587")
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
CONTENT_MAIN = str(ROOT / "Content Creation" / "main.py")
OUTPUT_DIR = ROOT / "Content Creation" / "output"


def get_today_dir() -> Path:
    """Get today's output directory."""
    today = datetime.now().strftime("%Y-%m-%d")
    return OUTPUT_DIR / today


def run_content_pipeline(mode: str) -> bool:
    """Run Content Creation/main.py --now {mode} --dry-run."""
    log.info(f"Running content pipeline: mode={mode}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "Content Creation")

    try:
        result = subprocess.run(
            [PYTHON, CONTENT_MAIN, "--now", mode, "--dry-run"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max
        )
        if result.returncode != 0:
            log.error(f"Pipeline failed (exit {result.returncode}):\n{result.stderr[-500:]}")
            return False
        log.info("Pipeline completed successfully")
        return True
    except subprocess.TimeoutExpired:
        log.error("Pipeline timed out after 5 minutes")
        return False
    except Exception as e:
        log.error(f"Pipeline error: {e}")
        return False


def collect_files(mode: str) -> tuple[list[Path], Path | None]:
    """Collect slide PNGs and copy kit from today's output folder.

    Returns (slides, copy_kit_path).
    """
    today_dir = get_today_dir()
    if not today_dir.exists():
        log.error(f"Output folder not found: {today_dir}")
        return [], None

    # Collect slides (sorted by name → slide order)
    slides = sorted(today_dir.glob("slide_*.png"))

    # Also grab any chart PNGs
    charts = sorted(today_dir.glob("chart_*.png"))

    all_images = slides + charts

    # Copy kit
    copy_kit = today_dir / f"copy_{mode}_market.txt"
    if not copy_kit.exists():
        copy_kit = None

    log.info(f"Collected {len(slides)} slides, {len(charts)} charts, copy_kit={'yes' if copy_kit else 'no'}")
    return all_images, copy_kit


def send_media_group(images: list[Path], caption: str = "") -> bool:
    """Send up to 10 images as a Telegram media group."""
    if not images:
        log.warning("No images to send")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"

    # Telegram limits media groups to 10 items
    batches = [images[i:i + 10] for i in range(0, len(images), 10)]

    for batch_idx, batch in enumerate(batches):
        media = []
        files = {}

        for i, img_path in enumerate(batch):
            attach_key = f"photo_{i}"
            media.append({
                "type": "photo",
                "media": f"attach://{attach_key}",
                **({"caption": caption, "parse_mode": "HTML"} if i == 0 and batch_idx == 0 else {}),
            })
            files[attach_key] = (img_path.name, open(img_path, "rb"), "image/png")

        payload = {
            "chat_id": CONTENT_CHAT_ID,
            "media": json.dumps(media),
        }

        for attempt in range(3):
            try:
                resp = requests.post(url, data=payload, files=files, timeout=120)
                if resp.ok:
                    log.info(f"Sent batch {batch_idx + 1}/{len(batches)} ({len(batch)} images)")
                    break
                else:
                    log.warning(f"Send attempt {attempt + 1}/3 failed: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                log.warning(f"Send attempt {attempt + 1}/3 error: {e}")

            if attempt < 2:
                time.sleep(2 ** attempt)
        else:
            log.error(f"Failed to send batch {batch_idx + 1} after 3 attempts")
            # Close file handles
            for f in files.values():
                f[1].close()
            return False

        # Close file handles
        for f in files.values():
            f[1].close()

        # Wait between batches to avoid rate limiting
        if batch_idx < len(batches) - 1:
            time.sleep(2)

    return True


def send_text_message(text: str) -> bool:
    """Send a plain text message to the channel."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CONTENT_CHAT_ID,
        "text": text[:4096],  # Telegram limit
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        if resp.ok:
            log.info("Copy kit sent")
            return True
        log.warning(f"Text send failed: {resp.status_code}")
        return False
    except Exception as e:
        log.error(f"Text send error: {e}")
        return False


def send_document(file_path: Path) -> bool:
    """Send a file as a document to the channel."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        try:
            resp = requests.post(
                url,
                data={"chat_id": CONTENT_CHAT_ID},
                files={"document": (file_path.name, f)},
                timeout=30,
            )
            if resp.ok:
                log.info(f"Document sent: {file_path.name}")
                return True
            log.warning(f"Document send failed: {resp.status_code}")
            return False
        except Exception as e:
            log.error(f"Document send error: {e}")
            return False


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("pre", "post"):
        print("Usage: python scripts/auto_content_post.py pre|post")
        sys.exit(1)

    mode = sys.argv[1]
    mode_label = "Pre-Market" if mode == "pre" else "Post-Market"

    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    log.info(f"=== {mode_label} Content Pipeline ===")

    # Step 1: Generate content
    success = run_content_pipeline(mode)
    if not success:
        send_text_message(f"⚠️ {mode_label} content generation FAILED. Check logs.")
        sys.exit(1)

    # Step 2: Collect output files
    images, copy_kit = collect_files(mode)

    if not images:
        send_text_message(f"⚠️ {mode_label} pipeline ran but no slides found.")
        sys.exit(1)

    # Step 3: Send images to Telegram
    caption = f"📱 <b>{mode_label} Carousel</b> — {datetime.now().strftime('%d %b %Y')}"
    sent_ok = send_media_group(images, caption=caption)

    # Step 4: Send copy kit as text
    if copy_kit:
        kit_text = copy_kit.read_text(encoding="utf-8")
        send_text_message(f"📝 <b>{mode_label} Copy Kit</b>\n\n{kit_text}")

    # Step 5: Send any JSON metadata as document
    today_dir = get_today_dir()
    for json_file in today_dir.glob("run_*.json"):
        send_document(json_file)

    log.info(f"=== {mode_label} Pipeline Complete ===")

    if not sent_ok:
        log.error("Telegram delivery failed — exiting with error so retry is possible.")
        sys.exit(1)


if __name__ == "__main__":
    main()
