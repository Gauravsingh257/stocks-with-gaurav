"""
dashboard/backend/routes/auth.py
Simple email/password authentication with JWT tokens.

Multi-replica JWT secret: When JWT_SECRET env var is unset, all replicas
must agree on the same secret or tokens issued by one replica fail to
validate on another. We resolve this by:

  1. Preferring env JWT_SECRET if set (operator-controlled).
  2. Else: read shared secret from Redis (`auth:jwt_secret`); first replica
     to start writes a strong random secret with SETNX so all subsequent
     replicas read the same value.
  3. Else: fall back to a fixed deterministic default. This last fallback
     keeps single-instance dev environments working but should never be
     hit on production (Redis is always available there).
"""

import logging
import os
import secrets
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Header
from pydantic import BaseModel

from dashboard.backend.db import get_connection
from dashboard.backend.ops_auth import verify_ops_key

router = APIRouter(tags=["auth"])
log = logging.getLogger("dashboard.auth")

_JWT_SECRET_REDIS_KEY = "auth:jwt_secret"
_JWT_SECRET_FALLBACK = "swg-default-secret-change-me-in-prod"


_jwt_secret_cache: str | None = None


def _resolve_jwt_secret() -> str:
    """Resolve the JWT secret with caching.

    Redis ALWAYS wins to prevent multi-replica split-brain where some
    replicas have env JWT_SECRET set and others use the default fallback.
    Order:
      1. Redis `auth:jwt_secret` — shared canonical secret across all replicas.
      2. If Redis is empty: seed with env JWT_SECRET (if set) or a fresh random.
         Uses SETNX so concurrent first-starts converge on the same value.
      3. Fixed fallback (dev only; only hit when Redis is unreachable).

    Called lazily before every encode/decode so a slow Redis bootstrap doesn't
    permanently pin replicas to the fallback secret.
    """
    global _jwt_secret_cache
    if _jwt_secret_cache is not None:
        return _jwt_secret_cache

    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is not None:
            existing = r.get(_JWT_SECRET_REDIS_KEY)
            if existing is not None:
                _jwt_secret_cache = existing.decode() if isinstance(existing, bytes) else str(existing)
                return _jwt_secret_cache
            # Seed Redis with env value if operator set one, else random.
            env_val = os.getenv("JWT_SECRET", "").strip()
            seed = env_val or secrets.token_urlsafe(48)
            if r.set(_JWT_SECRET_REDIS_KEY, seed, nx=True):
                log.warning(
                    "auth: seeded shared JWT secret in Redis (source=%s)",
                    "env" if env_val else "random",
                )
                _jwt_secret_cache = seed
                return _jwt_secret_cache
            existing = r.get(_JWT_SECRET_REDIS_KEY)
            if existing is not None:
                _jwt_secret_cache = existing.decode() if isinstance(existing, bytes) else str(existing)
                return _jwt_secret_cache
    except Exception as exc:
        log.warning("auth: redis-shared JWT secret unavailable (%s); using local fallback", exc)

    # Redis unreachable — local fallback. Prefer env var even now so dev with
    # JWT_SECRET set still works. Do NOT cache so we retry when Redis recovers.
    env_val = os.getenv("JWT_SECRET", "").strip()
    return env_val or _JWT_SECRET_FALLBACK


JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7


# Back-compat module attribute; reads through to the resolver each access.
class _JwtSecretProxy:
    def __str__(self) -> str:
        return _resolve_jwt_secret()
    def __eq__(self, other): return str(self) == other
    def encode(self, *a, **kw): return _resolve_jwt_secret().encode(*a, **kw)


JWT_SECRET = _JwtSecretProxy()


# ── DDL (called from schema.py init) ──────────────────────────────────────────

AUTH_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'FREE' CHECK(role IN ('FREE','PREMIUM','ADMIN')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS user_watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id),
    UNIQUE(user_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_user ON user_watchlist(user_id);
"""


def init_auth_tables() -> None:
    conn = get_connection()
    try:
        for stmt in AUTH_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _create_token(user_id: int, email: str, role: str) -> str:
    payload = {
        # Encode as string — PyJWT 2.10+ rejects integer `sub` on decode with
        # InvalidSubjectError. We coerce back to int in decode_token below.
        "sub": str(user_id),
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _resolve_jwt_secret(), algorithm=JWT_ALGORITHM)


def _bump_watchlist_os(uid: int) -> dict:
    """Invalidate live Redis slice and rebuild Watchlist OS after SQLite mutation. Returns ACK dict."""
    try:
        from dashboard.backend.routes.watchlist_os import _refresh_watchlist_os, invalidate_watchlist_os_cache

        invalidate_watchlist_os_cache(uid)
        return _refresh_watchlist_os(uid, trigger="mutation")
    except Exception as exc:
        log.warning("watchlist OS refresh after mutation failed uid=%s: %s", uid, exc)
        return {"persisted": False}


_DECODE_OPTS = {
    # PyJWT 2.10+ raises InvalidSubjectError when `sub` is a non-string.
    # Existing tokens in the wild have integer `sub`; disable strict sub typing.
    "verify_sub": False,
}


def _coerce_user(payload: dict) -> dict:
    """Normalize `sub` to integer for downstream code that expects int user_id."""
    sub = payload.get("sub")
    if isinstance(sub, str) and sub.isdigit():
        payload["sub"] = int(sub)
    return payload


def decode_token(token: str) -> dict:
    """Decode JWT. Disables strict sub-as-string check so existing integer-sub
    tokens validate, and coerces `sub` back to int for downstream consumers.
    """
    secret = _resolve_jwt_secret()
    try:
        return _coerce_user(jwt.decode(token, secret, algorithms=[JWT_ALGORITHM], options=_DECODE_OPTS))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        if secret != _JWT_SECRET_FALLBACK:
            try:
                return _coerce_user(jwt.decode(token, _JWT_SECRET_FALLBACK, algorithms=[JWT_ALGORITHM], options=_DECODE_OPTS))
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token expired")
            except jwt.InvalidTokenError:
                pass
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(authorization: str | None = Header(None)) -> dict:
    """FastAPI dependency: extract user from Authorization: Bearer <token>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    return decode_token(token)


def get_optional_user(authorization: str | None = Header(None)) -> dict | None:
    """Like get_current_user but returns None for unauthenticated requests."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token = authorization.split(" ", 1)[1]
        return decode_token(token)
    except HTTPException:
        return None


# ── Request/Response models ───────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/auth/register")
def register(req: RegisterRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (req.email.lower(),)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        hashed = _hash_password(req.password)
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (req.email.lower(), hashed, req.name),
        )
        conn.commit()
        user_id = cur.lastrowid
        token = _create_token(user_id, req.email.lower(), "FREE")
        return {"ok": True, "token": token, "user": {"id": user_id, "email": req.email.lower(), "name": req.name, "role": "FREE"}}
    finally:
        conn.close()


@router.post("/api/auth/login")
def login(req: LoginRequest):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (req.email.lower(),)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if not _verify_password(req.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = _create_token(row["id"], row["email"], row["role"])
        return {"ok": True, "token": token, "user": {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}}
    finally:
        conn.close()


@router.get("/api/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    conn = get_connection()
    try:
        row = conn.execute("SELECT id, email, name, role, created_at FROM users WHERE id = ?", (user["sub"],)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"], "created_at": row["created_at"]}
    finally:
        conn.close()


@router.post("/api/auth/upgrade")
def upgrade_to_premium(
    user: dict = Depends(get_current_user),
    _ops: None = Depends(verify_ops_key),
):
    """Admin-only role toggle — requires OPS_API_KEY header. Payment integration pending."""
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET role = 'PREMIUM' WHERE id = ?", (user["sub"],))
        conn.commit()
        return {"ok": True, "role": "PREMIUM"}
    finally:
        conn.close()


# ── Watchlist endpoints ───────────────────────────────────────────────────────

@router.get("/api/watchlist")
def get_watchlist(user: dict = Depends(get_current_user)):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, added_at FROM user_watchlist WHERE user_id = ? ORDER BY added_at DESC",
            (user["sub"],),
        ).fetchall()
        return {"items": [{"symbol": r["symbol"], "added_at": r["added_at"]} for r in rows]}
    finally:
        conn.close()


@router.post("/api/watchlist")
def add_to_watchlist(
    body: dict,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    raw = str(body.get("symbol", "") or "").strip().upper().replace("NSE:", "").strip()
    symbol = raw.replace(" ", "")
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES (?, ?)",
            (user["sub"], symbol),
        )
        conn.commit()
        uid = int(user["sub"])
        # Emit trace before background rebuild
        try:
            from dashboard.backend.routes.watchlist_os import _append_event_trace
            _append_event_trace(uid, "add", symbol, {"trigger": "post_api"})
        except Exception:
            pass
        ack = _bump_watchlist_os(uid)
        resp: dict = {"ok": True, "symbol": symbol}
        resp["persisted"] = ack.get("persisted", False)
        if ack.get("global_state_version") is not None:
            resp["global_state_version"] = ack["global_state_version"]
        if ack.get("bundle_revision") is not None:
            resp["snapshot_version"] = ack["bundle_revision"]
        return resp
    finally:
        conn.close()


@router.delete("/api/watchlist/{symbol}")
def remove_from_watchlist(
    symbol: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    conn = get_connection()
    try:
        sym = symbol.upper().replace("NSE:", "").strip()
        conn.execute(
            "DELETE FROM user_watchlist WHERE user_id = ? AND symbol = ?",
            (user["sub"], sym),
        )
        conn.commit()
        uid = int(user["sub"])
        # Emit trace before background rebuild
        try:
            from dashboard.backend.routes.watchlist_os import _append_event_trace
            _append_event_trace(uid, "remove", sym, {"trigger": "delete_api"})
        except Exception:
            pass
        ack = _bump_watchlist_os(uid)
        resp: dict = {"ok": True, "symbol": sym}
        resp["persisted"] = ack.get("persisted", False)
        if ack.get("global_state_version") is not None:
            resp["global_state_version"] = ack["global_state_version"]
        if ack.get("bundle_revision") is not None:
            resp["snapshot_version"] = ack["bundle_revision"]
        return resp
    finally:
        conn.close()
