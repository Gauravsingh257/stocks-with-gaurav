"""
Railway engine entrypoint: start /health server immediately, then run the full engine.
Fixes healthcheck failures — engine module load (pandas, kiteconnect, etc.) can take 60+ seconds.

Uses the real engine_api.app (which already has /health, /api/status, etc.) so that
set_state_reader() called later by the engine updates the SAME running server.
"""
import os
import sys
import threading
import time

_PORT = int(os.environ.get("PORT", 8000))


def _run_server():
    """Start the engine_api FastAPI app. It has /health built-in and is lightweight
    to import (no pandas/kiteconnect dependency)."""
    from dashboard.backend.engine_api import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")


def _run_trade_executor():
    """Start trade_executor_bot polling loop for 1-click Telegram execution."""
    try:
        from trade_executor_bot import poll_updates
        print("[BOOT] Trade executor bot started — listening for button clicks")
        poll_updates()
    except Exception as e:
        print(f"[BOOT] Trade executor bot failed: {e}")


def main():
    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    for i in range(30):
        try:
            import urllib.request
            req = urllib.request.urlopen(f"http://127.0.0.1:{_PORT}/health", timeout=2)
            if req.status == 200:
                print(f"[BOOT] Health server up on port {_PORT}")
                break
        except Exception:
            time.sleep(1)
    else:
        print("[BOOT] Health server did not respond in 30s")
        sys.exit(1)

    executor_thread = threading.Thread(target=_run_trade_executor, daemon=True)
    executor_thread.start()

    os.environ["SKIP_ENGINE_HTTP"] = "1"
    import smc_mtf_engine_v4
    smc_mtf_engine_v4.run_engine_main()


if __name__ == "__main__":
    main()
