# Go Live — One Command Setup

Run **`go_live.bat`** every morning. Paste the Zerodha `request_token` when prompted. That's it.

---

## One-Time Setup (5 minutes)

### 1. Create config file

Copy the example and add your backend URL:

```powershell
copy .go_live_config.example .go_live_config
notepad .go_live_config
```

Set:
```
BACKEND_URL=https://YOUR-RAILWAY-URL.up.railway.app
ENGINE_SERVICE=engine
WEB_SERVICE=web
KITE_API_KEY=your_zerodha_api_key
```

*(Get BACKEND_URL from Railway → web service → Settings → Domains. Get KITE_API_KEY from Zerodha app console.)*

### 2. Install Railway CLI (for auto token update)

```powershell
npm install -g @railway/cli
railway login
cd "C:\Users\g6666\Trading Algo"
railway link
```

When prompted, select your **project** and the **engine** service.

### 3. Engine must already be on Railway

If you haven't added the engine service yet, do it once (after pushing code with `go_live.bat` or `sync.bat`):

1. Railway → + New → GitHub Repo → same repo
2. Rename service to `engine`
3. Settings → Build → Builder: Dockerfile, Path: `Dockerfile.engine`
4. Variables → Add: `KITE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

---

## Daily Use

```
.\go_live.bat
```

1. Browser opens for Zerodha login
2. Log in, copy `request_token` from the redirect URL
3. Paste it when prompted
4. Script updates KITE_ACCESS_TOKEN on both engine and web services, pushes code, syncs trades

---

## If Railway CLI is not installed

The script will copy the token to your clipboard. Paste it into both:
**Railway → engine service → Variables → KITE_ACCESS_TOKEN** and
**Railway → web service → Variables → KITE_ACCESS_TOKEN** → Update
