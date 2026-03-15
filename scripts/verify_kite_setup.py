#!/usr/bin/env python3
"""
Verify Kite login flow and production setup.

Tests:
  1. Backend health endpoint reachable
  2. Health response: kite_connected, token_present, token_expires_in_hours, kite_disconnect_reason
  3. Login redirect endpoint (optional)
  4. Prints a readable result table

Usage:
  BACKEND_URL=https://your-railway.up.railway.app python scripts/verify_kite_setup.py

Requires: BACKEND_URL or NEXT_PUBLIC_BACKEND_URL in env (or .env / .go_live_config).
"""

import os
import sys
from pathlib import Path

# Repo root
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env or .go_live_config for BACKEND_URL
def _load_backend_url() -> str:
    url = os.getenv("BACKEND_URL") or os.getenv("NEXT_PUBLIC_BACKEND_URL") or ""
    url = (url or "").strip().rstrip("/")
    if url:
        return url
    for name in (".env", ".go_live_config"):
        p = _ROOT / name
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("BACKEND_URL=") or line.startswith("NEXT_PUBLIC_BACKEND_URL="):
                    key, _, val = line.partition("=")
                    val = val.strip().strip('"').strip("'").rstrip("/")
                    if val:
                        return val
    return ""




def _get_json(url: str) -> tuple[int, dict | None]:
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as r:
            import json
            return r.status, json.loads(r.read().decode())
    except Exception as e:
        err = str(e)
        if hasattr(e, "code"):
            return getattr(e, "code", 500), {"error": err}
        return -1, {"error": err}


def main() -> int:
    backend = _load_backend_url()
    if not backend:
        print("ERROR: Set BACKEND_URL or NEXT_PUBLIC_BACKEND_URL (env or .env or .go_live_config)")
        return 1

    print("=" * 60)
    print("Kite setup verification")
    print("=" * 60)
    print(f"Backend URL: {backend}")
    print()

    # 1. Health endpoint
    health_url = f"{backend}/api/system/health"
    status, data = _get_json(health_url)

    rows = []
    if status != 200:
        rows.append(("Backend health", "FAIL", f"HTTP {status}" + (f" — {data.get('error', '')}" if data else "")))
        for r in rows:
            print(f"  {r[0]:<28} | {r[1]:<6} | {r[2]}")
        print()
        return 1

    rows.append(("Backend health", "OK", "Reachable"))

    kite_connected = data.get("kite_connected", False)
    token_present = data.get("token_present", False)
    token_expires_in_hours = data.get("token_expires_in_hours")
    kite_disconnect_reason = data.get("kite_disconnect_reason")

    rows.append(("kite_connected", "YES" if kite_connected else "NO", ""))
    rows.append(("token_present", "YES" if token_present else "NO", ""))
    if token_expires_in_hours is not None:
        rows.append(("token_expires_in_hours", str(token_expires_in_hours), "hours"))
    else:
        rows.append(("token_expires_in_hours", "—", "no TTL (env/file token or missing)"))
    if kite_disconnect_reason:
        rows.append(("kite_disconnect_reason", kite_disconnect_reason, ""))

    # 2. Login endpoint (expect 302 redirect to Zerodha; do not follow redirects)
    import urllib.request
    import urllib.error

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    login_url = f"{backend}/api/kite/login"
    try:
        opener = urllib.request.build_opener(NoRedirect)
        r = opener.open(urllib.request.Request(login_url, method="GET"), timeout=15)
        login_status = r.status
    except urllib.error.HTTPError as e:
        login_status = e.code
    except Exception:
        login_status = -1
    if login_status in (302, 307, 301):
        rows.append(("Login redirect (/api/kite/login)", "OK", "302 redirect to Zerodha"))
    else:
        rows.append(("Login redirect (/api/kite/login)", "WARN" if login_status == 200 else "FAIL", f"HTTP {login_status}"))

    # Print table
    print("  " + "-" * 56)
    for r in rows:
        print(f"  {r[0]:<28} | {r[1]:<6} | {r[2]}")
    print("  " + "-" * 56)
    print()

    if not token_present:
        print("  Hint: Open /api/kite/login (or your frontend proxy) and log in to Zerodha to store token in Redis.")
    if not kite_connected and token_present:
        print("  Hint: Token present but kite_connected=false — token may be expired. Log in again at /api/kite/login.")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
