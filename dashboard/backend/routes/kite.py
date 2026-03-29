"""
dashboard/backend/routes/kite.py
Kite Connect web login flow: redirect to Zerodha, callback to store token in Redis.

GET  /api/kite/login   — redirect to Zerodha login page
GET  /api/kite/callback — exchange request_token, store access_token in Redis
POST /api/kite/token   — accept request_token or full URL, exchange and store (for manual paste)
"""

import re
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

from dashboard.backend import kite_auth

router = APIRouter(prefix="/api/kite", tags=["kite"])


def _extract_request_token(value: str) -> str | None:
    """Extract request_token from full URL or return value as-is if it looks like a raw token."""
    s = (value or "").strip()
    if not s:
        return None
    # If it looks like a URL with request_token=, extract it
    m = re.search(r"request_token=([^&\s]+)", s)
    if m:
        return m.group(1).strip()
    # Otherwise treat as raw token
    return s if len(s) >= 5 else None


@router.get("/login")
def kite_login():
    """Redirect admin to Zerodha Kite login page. After login, Zerodha redirects to /api/kite/callback."""
    try:
        url = kite_auth.get_login_url()
        return RedirectResponse(url=url)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Failed to build login URL. Check KITE_API_KEY."},
        )


@router.get("/callback")
def kite_callback(request_token: str | None = Query(None, alias="request_token")):
    """
    Handle Zerodha redirect after login: exchange request_token for access_token, store in Redis.
    Returns JSON success; optional future: redirect to frontend "Login successful" page.
    """
    if not request_token or not request_token.strip():
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "Missing request_token. Complete login at /api/kite/login.",
            },
        )
    try:
        access_token = kite_auth.generate_access_token(request_token.strip())
        kite_auth.store_access_token(access_token)
        # Ensure all consumers use the new token on next use
        try:
            from dashboard.backend.routes.charts import _reset_kite
            _reset_kite()
        except Exception:
            pass
        try:
            from dashboard.backend.realtime import request_reconnect
            request_reconnect()
        except Exception:
            pass
        return {"status": "connected", "message": "Kite session established", "access_token": access_token}
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid or expired request token."},
        )
    except RuntimeError as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": str(e)},
        )
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Could not establish session: {e}. Try logging in again."},
        )


class TokenInput(BaseModel):
    """Body for POST /api/kite/token — paste full redirect URL or raw request_token."""
    request_token: str = ""


@router.post("/token")
def kite_token_from_paste(body: TokenInput):
    """
    Accept request_token or full callback URL (e.g. from Zerodha redirect).
    Exchanges for access_token, stores in Redis — used everywhere (dashboard, engine).
    """
    raw = (body.request_token or "").strip()
    request_token = _extract_request_token(raw)
    if not request_token:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "Paste the full redirect URL or request_token from Zerodha login.",
            },
        )
    try:
        access_token = kite_auth.generate_access_token(request_token)
        kite_auth.store_access_token(access_token)
        try:
            from dashboard.backend.routes.charts import _reset_kite
            _reset_kite()
        except Exception:
            pass
        try:
            from dashboard.backend.realtime import request_reconnect
            request_reconnect()
        except Exception:
            pass
        return {
            "status": "connected",
            "message": "Kite session established via paste.",
            "access_token": access_token,
        }
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid or expired request token. Log in again."},
        )
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(e)},
        )


@router.get("/current-token")
def kite_current_token():
    """
    Return the current access_token from Redis so local scripts can sync.
    Used by morning_login.ps1 to save token locally after Railway login.
    """
    try:
        token = kite_auth.get_access_token()
        if token:
            return {"status": "ok", "access_token": token}
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "No token found in Redis."},
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )


class AccessTokenInput(BaseModel):
    """Body for POST /api/kite/store-token — push a pre-exchanged access_token."""
    access_token: str


@router.post("/store-token")
def kite_store_token(body: AccessTokenInput):
    """
    Accept a pre-exchanged access_token and store it in Redis.
    Used by auto_login.py to push the token from local machine to Railway.
    """
    token = (body.access_token or "").strip()
    if not token or len(token) < 10:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid access_token."},
        )
    try:
        kite_auth.store_access_token(token)
        try:
            from dashboard.backend.routes.charts import _reset_kite
            _reset_kite()
        except Exception:
            pass
        try:
            from dashboard.backend.realtime import request_reconnect
            request_reconnect()
        except Exception:
            pass
        return {"status": "ok", "message": "Token stored in Redis."}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )
