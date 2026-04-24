"""
dashboard/backend/routes/auth.py
Simple email/password authentication with JWT tokens.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel

from dashboard.backend.db import get_connection

router = APIRouter(tags=["auth"])
log = logging.getLogger("dashboard.auth")

JWT_SECRET = os.getenv("JWT_SECRET", "swg-default-secret-change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7


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
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
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
def upgrade_to_premium(user: dict = Depends(get_current_user)):
    """Placeholder for payment integration — manually toggles role to PREMIUM."""
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
def add_to_watchlist(body: dict, user: dict = Depends(get_current_user)):
    symbol = body.get("symbol", "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES (?, ?)",
            (user["sub"], symbol),
        )
        conn.commit()
        return {"ok": True, "symbol": symbol}
    finally:
        conn.close()


@router.delete("/api/watchlist/{symbol}")
def remove_from_watchlist(symbol: str, user: dict = Depends(get_current_user)):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM user_watchlist WHERE user_id = ? AND symbol = ?",
            (user["sub"], symbol.upper()),
        )
        conn.commit()
        return {"ok": True, "symbol": symbol.upper()}
    finally:
        conn.close()
