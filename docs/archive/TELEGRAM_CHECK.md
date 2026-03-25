# Telegram not sending — Checklist

If you ran `run_live_v4.bat` but **no message came in Telegram**, follow these steps.

---

## 1. Check the engine console

When the engine starts, it sends a startup message. If Telegram **fails**, it only **prints** the error (it does not crash).

- Look at the **run_live_v4** window for a line like:
  - `Telegram error: ...`
- If you see that, the message after the colon is the reason (e.g. wrong token, invalid chat_id, connection error).

---

## 2. Set Telegram env variables

The engine uses:

- **TELEGRAM_BOT_TOKEN** — from [@BotFather](https://t.me/BotFather) (e.g. `123456:ABC-DEF...`).
- **TELEGRAM_CHAT_ID** — the chat/group where the bot should send messages.

**Option A — .env in project root**

Create or edit `C:\Users\g6666\Trading Algo\.env`:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

**Option B — System env (Windows)**

1. Win + R → `sysdm.cpl` → Advanced → Environment Variables.
2. Add User variables: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

**Option C — Set in the batch file**

Edit `run_live_v4.bat` and add before the `python` line:

```bat
set TELEGRAM_BOT_TOKEN=your_bot_token_here
set TELEGRAM_CHAT_ID=your_chat_id_here
```

Then run `run_live_v4.bat` again.

---

## 3. Get Bot Token and Chat ID

**Bot token**

1. Open Telegram → search **@BotFather**.
2. Send `/newbot` (or use an existing bot).
3. Copy the token BotFather gives you (e.g. `123456789:ABCdefGHI...`).  
   → Use it as **TELEGRAM_BOT_TOKEN**.

**Chat ID**

1. Add your bot to the group (or start a private chat with it).
2. Send any message in that chat.
3. Open in browser (use your bot token):
   - `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. In the JSON, find `"chat":{"id": -1001234567890}`.  
   → That number is **TELEGRAM_CHAT_ID** (often negative for groups).

---

## 4. Test Telegram from your PC

In the project root, run:

```bat
python test_telegram.py
```

(Use the script below.) If you get a message in Telegram, the token and chat_id are correct and the problem is likely in the engine (e.g. env not loaded when running from the batch file).

---

## 5. Ensure `requests` is installed

The engine uses `requests` to call the Telegram API. If it’s missing, you’ll see an error in the console.

```bat
pip install requests
```

---

## 6. If the engine exits before any Telegram message

If the window closes quickly or you see:

- `ABORTING ENGINE STARTUP — TOKEN TOO OLD`
- `ABORTING ENGINE STARTUP DUE TO DATA FAILURE`

then:

1. Run **zerodha_login** (e.g. `run_login.bat`) and paste the new `request_token`.
2. Ensure **access_token.txt** exists in the project root (or **KITE_ACCESS_TOKEN** is set).
3. Run **run_live_v4.bat** again.

The **first** Telegram message is sent when the script **loads** (before the main loop). The **second** (“V4 INSTITUTIONAL ENGINE :: ONLINE”) is sent only after token and data checks pass inside `run_live_mode()`.

---

## Quick summary

| Step | Action |
|------|--------|
| 1 | Check run_live_v4 window for `Telegram error: ...` |
| 2 | Set **TELEGRAM_BOT_TOKEN** and **TELEGRAM_CHAT_ID** (env or .env) |
| 3 | Get token from @BotFather and chat id from getUpdates |
| 4 | Run `python test_telegram.py` to test send |
| 5 | Run `pip install requests` if needed |
| 6 | Refresh Kite token if engine aborts on startup |
