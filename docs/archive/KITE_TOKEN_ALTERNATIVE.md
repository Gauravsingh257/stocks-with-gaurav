# Kite Token — Alternative to URL Login

If the **URL method** (`/api/kite/login` → Zerodha → `/api/kite/callback`) does not work (e.g. redirect URI, Redis not reachable, or callback not loading), use the **environment variable method** below.

---

## 1. Get a fresh access token locally

Run on your machine (same repo, with Kite credentials set):

```bash
python zerodha_login.py
```

- Your browser opens the Zerodha login page.
- Log in with your Zerodha credentials (password + OTP).
- Zerodha redirects to a URL that contains `request_token=...` in the address bar.
- Copy the **request_token** value from that URL and paste it into the terminal when the script asks.
- The script prints the **access_token** and saves it to `access_token.txt`.

Example output:

```
ACCESS TOKEN: xxxxxxxxxxxxxxxxxxxxx
Token saved to access_token.txt
```

Copy that **access_token** value (the long string).

---

## 2. Set the token in Railway (or env)

### Option A — Railway Variables (recommended for cloud)

1. Open **Railway** → your project.
2. For **both** the **web** (dashboard) and **engine** services:
   - Go to **Variables**.
   - Add or edit:
     - **KITE_ACCESS_TOKEN** = paste the access token you copied.
   - Save / redeploy if needed.

The dashboard and engine will use this token. Token expires in ~24 hours; repeat steps 1–2 daily (or when you see "token expired" / Kite disconnected).

### Option B — Local development

- **Option B1:** Create or edit `access_token.txt` in the project root and paste the access token (one line, no quotes). The app reads it via `config/kite_auth.py`.
- **Option B2:** Set env before running:
  ```powershell
  $env:KITE_ACCESS_TOKEN = "paste_token_here"
  python smc_mtf_engine_v4.py
  ```
  Or in `.env` / `.go_live_config`:
  ```
  KITE_ACCESS_TOKEN=paste_token_here
  ```

---

## 3. Required variables

| Variable | Where | Purpose |
|----------|--------|---------|
| **KITE_API_KEY** | Railway (both services) + zerodha_login | Your Zerodha API key (app console). |
| **KITE_ACCESS_TOKEN** | Railway (both services) or access_token.txt | Session token from zerodha_login (refresh daily). |
| **KITE_API_SECRET** | Only for zerodha_login / URL callback | Used to exchange request_token → access_token. Not needed at runtime if you set KITE_ACCESS_TOKEN manually. |

---

## Summary

- **URL method:** `/api/kite/login` → login on Zerodha → callback stores token in Redis. Needs Redis + correct redirect URI.
- **Other way:** Run `python zerodha_login.py` locally → copy **access_token** → set **KITE_ACCESS_TOKEN** in Railway (and/or use `access_token.txt` locally). No URL or Redis needed for token loading once the variable is set.
