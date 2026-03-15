#!/usr/bin/env python3
"""
Quick test: send one Telegram message using TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
Run from project root:  python test_telegram.py
"""
import os

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        print("Set them in .env or environment, then run again.")
        return
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": "Test from SMC Engine — if you see this, Telegram is working.",
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if r.ok:
            print("Message sent. Check your Telegram.")
        else:
            print("Telegram API error:", r.status_code, r.text)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
