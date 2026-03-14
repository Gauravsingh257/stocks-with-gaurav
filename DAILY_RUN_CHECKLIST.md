# Daily Algo Run Checklist

## 1. Login (Every Morning)
*   **Run `run_login.bat`** (Double-click).
*   Browser will open Zerodha login page. **Login**.
*   After successful login, you will be redirected to a URL like:
    `https://kite.zerodha.com/?request_token=YOUR_TOKEN_HERE&action=login`
*   **Copy** the `request_token` value from the address bar.
*   **Paste** it into the black terminal window and press **Enter**.
*   Wait for: `✅ Token saved to access_token.txt`.
*   *(Note: This token is valid for 24 hours).*

## 2. Start Engine
*   **Run `run_live_v4.bat`** (Double-click).
*   Wait for these messages:
    -   `Zerodha Kite Connected`
    -   `SMC ENGINE LIVE`
    -   `[INFO] Waiting for next candle close...`
*   The engine is now running. **DO NOT CLOSE THIS WINDOW.**

## 3. During the Day
*   **Keep PC ON** and connected to the internet.
*   **Monitor Telegram** for `PRO SMC UPDATE` trade alerts.
*   **Do not** manually interfere with active trades unless necessary.

## Troubleshooting
*   **"access_token.txt not found"**: Run Step 1 again.
*   **"Connection Failed"**: Check internet or re-run Step 1.
*   **Window closes immediately**: Screen record the error or check logs.
