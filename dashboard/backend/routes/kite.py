"""
dashboard/backend/routes/kite.py
Kite Connect web login flow: redirect to Zerodha, callback to store token in Redis.

GET /api/kite/login   — redirect to Zerodha login page
GET /api/kite/callback — exchange request_token, store access_token in Redis
"""

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse, JSONResponse

from dashboard.backend import kite_auth

router = APIRouter(prefix="/api/kite", tags=["kite"])


@router.get("/login")
def kite_login():
    """Redirect admin to Zerodha Kite login page. After login, Zerodha redirects to /api/kite/callback."""
    try:
        url = kite_auth.get_login_url()
        return RedirectResponse(url=url)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Failed to build login URL", "detail": str(e)},
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
        return {"status": "connected", "message": "Kite session established"}
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid or expired request token."},
        )
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Could not establish session. Try logging in again."},
        )
