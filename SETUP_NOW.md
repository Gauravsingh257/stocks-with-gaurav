# Setup Now — Complete These 4 Steps

Config file created. Railway CLI installed. **You need to do these 4 steps** (they require your browser/login):

---

## Step 1: Fix your backend URL in config

1. Open Railway → your **backend** service → **Settings** → **Domains**
2. Copy your URL (e.g. `https://stocks-with-gaurav-production.up.railway.app`)
3. Open `C:\Users\g6666\Trading Algo\.go_live_config` in Notepad
4. Replace `YOUR-RAILWAY-URL` with your actual URL (or paste the full URL over the placeholder)
   - Example: `BACKEND_URL=https://stocks-with-gaurav-production.up.railway.app`
5. Save and close

---

## Step 2: Railway login + link

Open **PowerShell** or **Command Prompt** and run:

```powershell
cd "C:\Users\g6666\Trading Algo"
railway login
```

→ Browser opens. Log in with your Railway account.

Then:

```powershell
railway link
```

→ Select your **project**, then select the **engine** service (or the service you named "engine").

---

## Step 3: Add engine service on Railway (if not there yet)

If you already have an engine service, skip this.

1. Go to [railway.app](https://railway.app) → your project
2. Click **+ New** → **GitHub Repo**
3. Select the **same repo** as your backend
4. Rename the new service to `engine`
5. **Settings** → **Build**:
   - Builder: **Dockerfile**
   - Dockerfile Path: `Dockerfile.engine`
6. **Variables** → Add:
   - `KITE_API_KEY` = your Zerodha API key
   - `TELEGRAM_BOT_TOKEN` = your Telegram bot token
   - `TELEGRAM_CHAT_ID` = your chat ID

*(KITE_ACCESS_TOKEN will be set automatically by go_live.bat each day.)*

---

## Step 4: Push code (first time)

Run once to push the engine code to GitHub:

```powershell
cd "C:\Users\g6666\Trading Algo"
.\sync.bat
```

Type a commit message (e.g. `Engine deploy`) and press Enter.

---

## Done

After these 4 steps, your daily routine is just:

```
.\go_live.bat
```

Paste the `request_token` when prompted.
