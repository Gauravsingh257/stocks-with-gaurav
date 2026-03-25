# Morning Process — Before 9:15 IST

Do these steps **every trading day** before market open (9:15 IST). Kite tokens expire daily, so token refresh is required each morning.

---

## Step 1 — Refresh Kite token

Choose **one** of the two methods.

### Option A: URL login (recommended if dashboard is already running)

1. Open your dashboard in the browser (e.g. **https://stockswithgaurav.com** or your Railway/Vercel URL).
2. Go to the Kite login URL:
   - **https://&lt;your-site&gt;/api/kite/login**
   - Or click **“Connect Kite”** / **“Log in to Kite”** if your UI has it.
3. Log in with your Zerodha credentials (and complete 2FA if asked).
4. You will be redirected back; the callback saves the new token in **Redis**.
5. **Verify:** Open **https://&lt;your-site&gt;/api/system/kite-status**  
   - You should see `"token_valid": true` and `"token_source": "redis"`.

**Result:** Dashboard (charts, realtime ticks, worker) will use the new token. No file or env update needed if everything reads from Redis.

---

### Option B: Script (for local engine + file-based token)

Use this when the **trading engine** runs locally and reads token from a file or env.

1. Open a terminal in the project root:  
   `C:\Users\g6666\Trading Algo`
2. Run the login script:
   - **Double-click:** `run_login.bat`  
   - **Or:** `python zerodha_login.py`
3. A browser window opens → log in with Zerodha → after redirect, copy the **request_token** from the URL (the part after `request_token=`).
4. Paste the request_token into the terminal when prompted and press Enter.
5. The script writes the new token to **access_token.txt** in the project root.
6. **If you use Railway/dashboard:** Update **KITE_ACCESS_TOKEN** in Railway Variables with the new token and redeploy (or use Option A so Redis has the token and dashboard uses it).

**Result:** Local engine and any process using `access_token.txt` or `KITE_ACCESS_TOKEN` will use the new token.

---

## Step 2 — Start the trading engine (before 9:15 IST)

The strategy engine runs **separately** from the dashboard (e.g. on your PC). Start it before market open.

1. Open a terminal (or Command Prompt) in the project root:  
   `C:\Users\g6666\Trading Algo`
2. Start the live engine:
   - **Double-click:** `run_live_v4.bat`  
   - **Or:** `python smc_mtf_engine_v4.py`
3. Leave this window open. You should see logs like “Scanning …”, “Engine Alive”, etc.
4. **Optional:** Start **market_engine** worker if you use it (OHLC/OI cache):
   - `python scripts/market_engine.py`  
   - Requires **REDIS_URL** and Kite credentials (env or Redis token from Step 1 Option A).

**Result:** Engine generates signals and monitors trades; dashboard shows engine state when connected.

---

## Step 3 — Keep approvals on for live execution

Agent actions (e.g. Trade Manager) go through an **approval queue**. No trade is executed until you approve it.

### How approvals work

- Agents propose actions (e.g. place order, move SL) and they are stored in **agent_action_queue** with status **PENDING**.
- You must **approve** (or reject) each action before it is executed.
- This is by design: “Never execute trades without explicit confirmation in live mode.”

### Where to approve

1. **Dashboard → AI Signals / Agents**  
   - Open the **Agents** (or “AI Signals”) section.
   - Look for **Actions Proposed** or **Pending actions**.
   - Use **Approve** / **Reject** for each pending action.

2. **API (if no UI yet)**  
   - List pending:  
     `GET https://<your-backend>/api/agents/queue?status=PENDING`
   - Approve one:  
     `POST https://<your-backend>/api/agents/queue/<action_id>/approve`
   - Reject one:  
     `POST https://<your-backend>/api/agents/queue/<action_id>/reject`

3. **Telegram**  
   - If you have trade buttons in Telegram, use them to approve or reject when the engine sends a signal.

**Result:** Only actions you approve are executed; rejected ones stay in the queue with status REJECTED.

---

## Quick checklist (copy for each morning)

| # | Task | Done |
|---|------|------|
| 1 | Refresh Kite token (URL login **or** run_login.bat / zerodha_login.py) | ☐ |
| 2 | Verify token: `/api/system/kite-status` shows `token_valid: true` (if using dashboard) | ☐ |
| 3 | Start trading engine: `run_live_v4.bat` or `python smc_mtf_engine_v4.py` | ☐ |
| 4 | (Optional) Start worker: `python scripts/market_engine.py` | ☐ |
| 5 | Confirm approvals: check Dashboard → Agents / Actions and approve any PENDING actions before execution | ☐ |

---

## Summary

- **Token:** Refresh every morning via URL (`/api/kite/login`) or script (`run_login.bat` / `zerodha_login.py`).
- **Engine:** Start before 9:15 IST with `run_live_v4.bat` (or `python smc_mtf_engine_v4.py`); optionally start `scripts/market_engine.py` if you use it.
- **Approvals:** Always review and approve (or reject) pending actions in the dashboard (or via API/Telegram) so only approved actions are executed in live mode.
