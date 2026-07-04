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

# SECURITY: never ship a known/constant fallback secret. If it lands in the
# source (or a leaked repo) anyone can forge a valid token for any user/role.
# When neither env nor Redis can supply a secret (Redis outage in dev), we fall
# back to a per-process RANDOM value generated once at import. It is unguessable,
# so it cannot be used to forge tokens; the trade-off is that tokens issued
# during such an outage stop validating once Redis recovers (acceptable — a
# rare degraded window forces a harmless re-login, not an account takeover).
_EPHEMERAL_PROCESS_SECRET = secrets.token_urlsafe(48)


_jwt_secret_cache: str | None = None


def _resolve_jwt_secret() -> str:
    """Resolve the JWT secret with caching.

    Redis ALWAYS wins to prevent multi-replica split-brain where some
    replicas have env JWT_SECRET set and others use the default fallback.
    Order:
      1. Redis `auth:jwt_secret` — shared canonical secret across all replicas.
      2. If Redis is empty: seed with env JWT_SECRET (if set) or a fresh random.
         Uses SETNX so concurrent first-starts converge on the same value.
      3. Per-process RANDOM secret (only hit when Redis is unreachable and no
         env var is set). Never a shipped constant — see _EPHEMERAL_PROCESS_SECRET.

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

    # Redis unreachable — local fallback. Prefer env var even now so dev/prod
    # with JWT_SECRET set still works. Otherwise use the per-process RANDOM
    # secret (never a shipped constant). Do NOT cache so we retry when Redis
    # recovers and re-converge on the shared secret.
    env_val = os.getenv("JWT_SECRET", "").strip()
    return env_val or _EPHEMERAL_PROCESS_SECRET


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

CREATE TABLE IF NOT EXISTS user_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    stop_loss       REAL,
    target_1        REAL,
    target_2        REAL,
    holding_period  TEXT NOT NULL DEFAULT 'Swing',
    taken_at        TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','TARGET_HIT','SL_HIT','CLOSED')),
    exit_price      REAL,
    exit_reason     TEXT,
    exited_at       TEXT,
    pnl_r           REAL,
    notes           TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_positions_user ON user_positions(user_id, status);
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
        log.exception("watchlist OS refresh after mutation failed uid=%s", uid)
        return {"persisted": False, "stage": "bump", "error": f"{type(exc).__name__}: {exc}"}


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
        # SECURITY: do NOT retry against any other secret. A token that fails
        # the resolved secret is invalid, full stop. (The old code re-tried a
        # hardcoded constant here, which let anyone forge tokens for any user.)
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
        if ack.get("stage"):
            resp["persist_stage_failed"] = ack["stage"]
        if ack.get("error"):
            resp["persist_error"] = ack["error"]
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


# ── User Positions (Take Entry → live tracking) ───────────────────────────────

class TakeEntryRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    holding_period: str | None = "Swing"
    notes: str | None = None


def _normalize_symbol(s: str) -> str:
    return str(s or "").strip().upper().replace("NSE:", "").replace(" ", "")


@router.post("/api/watchlist/positions")
def take_entry(req: TakeEntryRequest, user: dict = Depends(get_current_user)):
    """Open a new active position from the watchlist 'Take Entry' button.
    Persists to user_positions SQLite table. CMP + P&L computed at read time.
    """
    sym = _normalize_symbol(req.symbol)
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    if not req.entry_price or req.entry_price <= 0:
        raise HTTPException(status_code=400, detail="entry_price must be > 0")
    conn = get_connection()
    try:
        # Block duplicate active positions for the same symbol per user
        existing = conn.execute(
            "SELECT id FROM user_positions WHERE user_id = ? AND symbol = ? AND status = 'ACTIVE'",
            (user["sub"], sym),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"Active position already exists for {sym}")

        cur = conn.execute(
            """
            INSERT INTO user_positions
                (user_id, symbol, entry_price, stop_loss, target_1, target_2, holding_period, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["sub"], sym,
                float(req.entry_price),
                float(req.stop_loss) if req.stop_loss is not None else None,
                float(req.target_1) if req.target_1 is not None else None,
                float(req.target_2) if req.target_2 is not None else None,
                (req.holding_period or "Swing").strip()[:32],
                (req.notes or "").strip()[:240] or None,
            ),
        )
        conn.commit()
        pos_id = cur.lastrowid
        # G2-3 SHADOW: record canonical lifecycle event (best-effort, never raises).
        try:
            from dashboard.backend.lifecycle_ledger import record_lifecycle_event

            record_lifecycle_event(
                sym, "ENTRY_ACTIVE",
                source="take_entry", position_id=pos_id, user_id=user["sub"],
                planned_entry=float(req.entry_price),
                stop_loss=float(req.stop_loss) if req.stop_loss is not None else None,
                target_1=float(req.target_1) if req.target_1 is not None else None,
                target_2=float(req.target_2) if req.target_2 is not None else None,
                manual_override=True,  # user-clicked, not zone-activated (pre-canonical)
                details={"holding_period": req.holding_period},
            )
        except Exception:
            pass
        return {
            "ok": True,
            "id": pos_id,
            "symbol": sym,
            "entry_price": req.entry_price,
            "status": "ACTIVE",
        }
    finally:
        conn.close()


def _resolve_position_cmp(symbol: str) -> tuple[float | None, str | None]:
    """Best-effort CMP for an active position. Returns (price, source)."""
    sym = _normalize_symbol(symbol)
    # 1. Engine snapshot equity_ltp (freshest during market hours)
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap = get_engine_snapshot() or {}
        eq = snap.get("equity_ltp")
        if isinstance(eq, dict):
            v = eq.get(sym) or eq.get(f"NSE:{sym}")
            if isinstance(v, (int, float)) and v > 0:
                return float(v), "engine_ltp"
    except Exception:
        pass
    # 2. Redis ltp:{SYM}
    try:
        from dashboard.backend.cache import get_ltp
        p = get_ltp(sym)
        if p is not None:
            return float(p), "redis_ltp"
    except Exception:
        pass
    # 3. equity:ltp:latest hash
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is not None:
            raw = r.hget("equity:ltp:latest", sym)
            if raw is not None:
                return float(raw if isinstance(raw, (int, float, str)) else raw.decode()), "ltp_hash"
    except Exception:
        pass
    return None, None


def _classify_position(entry: float, cmp: float | None, sl: float | None, t1: float | None, t2: float | None) -> str:
    """Derive a status hint independent of stored status — for live display."""
    if cmp is None or entry <= 0:
        return "Active"
    if sl is not None and cmp <= sl * 1.005:
        return "SL Risk"
    target = t2 or t1
    if target is not None and cmp >= target * 0.995:
        return "Near Target"
    pnl_pct = (cmp - entry) / entry * 100
    if pnl_pct >= 5:
        return "Running"
    if pnl_pct <= -3:
        return "Underwater"
    return "Active"


@router.get("/api/watchlist/positions")
def list_positions(
    status: str = "ACTIVE",
    user: dict = Depends(get_current_user),
):
    """Return user's active (or closed) positions enriched with live CMP + P&L."""
    valid = {"ACTIVE", "CLOSED", "ALL"}
    s = status.upper()
    if s not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")
    conn = get_connection()
    try:
        if s == "ALL":
            rows = conn.execute(
                "SELECT * FROM user_positions WHERE user_id = ? ORDER BY taken_at DESC",
                (user["sub"],),
            ).fetchall()
        elif s == "CLOSED":
            rows = conn.execute(
                "SELECT * FROM user_positions WHERE user_id = ? AND status != 'ACTIVE' ORDER BY taken_at DESC",
                (user["sub"],),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_positions WHERE user_id = ? AND status = 'ACTIVE' ORDER BY taken_at DESC",
                (user["sub"],),
            ).fetchall()
    finally:
        conn.close()

    items: list[dict] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        d = dict(row)
        entry = float(d["entry_price"])
        cmp_val, cmp_src = _resolve_position_cmp(d["symbol"]) if d.get("status") == "ACTIVE" else (None, None)
        sl = float(d["stop_loss"]) if d.get("stop_loss") is not None else None
        t1 = float(d["target_1"]) if d.get("target_1") is not None else None
        t2 = float(d["target_2"]) if d.get("target_2") is not None else None
        risk = abs(entry - sl) if sl is not None else None
        pnl_pct = None
        pnl_r = None
        if cmp_val is not None and entry > 0:
            pnl_pct = round((cmp_val - entry) / entry * 100, 2)
            if risk and risk > 0:
                pnl_r = round((cmp_val - entry) / risk, 2)
        # Holding days
        try:
            taken_dt = datetime.fromisoformat(str(d.get("taken_at")).replace(" ", "T"))
            if taken_dt.tzinfo is None:
                taken_dt = taken_dt.replace(tzinfo=timezone.utc)
            holding_days = max(0, (now - taken_dt).days)
        except Exception:
            holding_days = None

        live_status = _classify_position(entry, cmp_val, sl, t1, t2) if d.get("status") == "ACTIVE" else d.get("status")

        # For CLOSED positions, prefer stored exit-based P&L (DB pnl_r); also
        # derive pnl_pct from exit_price so the UI doesn't show '—'.
        if d.get("status") != "ACTIVE":
            stored_pnl_r = d.get("pnl_r")
            if stored_pnl_r is not None:
                try:
                    pnl_r = float(stored_pnl_r)
                except (TypeError, ValueError):
                    pass
            exit_price = d.get("exit_price")
            if exit_price is not None and pnl_pct is None and entry > 0:
                try:
                    pnl_pct = round((float(exit_price) - entry) / entry * 100, 2)
                except (TypeError, ValueError):
                    pass

        # CMP: prefer live resolve; fall back to the price the shared
        # PositionTrackingService last wrote to user_positions.current_price
        # (so the card still shows a price off-hours / when resolve misses).
        cmp_out = cmp_val if cmp_val is not None else d.get("current_price")
        items.append({
            "id": d.get("id"),
            "symbol": d.get("symbol"),
            "entry_price": entry,
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "holding_period": d.get("holding_period"),
            "taken_at": d.get("taken_at"),
            "status": d.get("status"),
            "live_status": live_status,
            "cmp": cmp_out,
            "cmp_source": cmp_src,
            "pnl_pct": pnl_pct,
            "pnl_r": pnl_r,
            "holding_days": holding_days,
            "source": d.get("source"),
            "quantity": d.get("quantity"),
            "exit_price": d.get("exit_price"),
            "exit_reason": d.get("exit_reason"),
            "exited_at": d.get("exited_at"),
            "notes": d.get("notes"),
        })
    return {"items": items, "count": len(items), "status_filter": s}


class ClosePositionRequest(BaseModel):
    exit_price: float | None = None
    exit_reason: str | None = None  # e.g. "manual", "target_hit", "sl_hit"


@router.delete("/api/watchlist/positions/{position_id}")
def close_position(
    position_id: int,
    body: ClosePositionRequest | None = None,
    user: dict = Depends(get_current_user),
):
    """Close an active position. If exit_price not supplied, uses live CMP."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM user_positions WHERE id = ? AND user_id = ?",
            (position_id, user["sub"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="position not found")
        if row["status"] != "ACTIVE":
            raise HTTPException(status_code=409, detail=f"position already {row['status']}")

        exit_price = None
        exit_reason = (body.exit_reason if body else None) or "manual"
        if body and body.exit_price is not None:
            exit_price = float(body.exit_price)
        else:
            cmp_val, _ = _resolve_position_cmp(row["symbol"])
            if cmp_val is not None:
                exit_price = cmp_val

        entry = float(row["entry_price"])
        sl = float(row["stop_loss"]) if row["stop_loss"] is not None else None
        pnl_r = None
        new_status = "CLOSED"
        if exit_price is not None:
            risk = abs(entry - sl) if sl else None
            if risk and risk > 0:
                pnl_r = round((exit_price - entry) / risk, 2)
            # Auto-classify if hit target/SL
            t1 = float(row["target_1"]) if row["target_1"] is not None else None
            t2 = float(row["target_2"]) if row["target_2"] is not None else None
            target = t2 or t1
            if sl is not None and exit_price <= sl * 1.005:
                new_status = "SL_HIT"
            elif target is not None and exit_price >= target * 0.995:
                new_status = "TARGET_HIT"

        conn.execute(
            """
            UPDATE user_positions
            SET status = ?, exit_price = ?, exit_reason = ?, pnl_r = ?, exited_at = datetime('now')
            WHERE id = ?
            """,
            (new_status, exit_price, exit_reason, pnl_r, position_id),
        )
        conn.commit()
        # G2-3 SHADOW: canonical close event (best-effort, never raises).
        try:
            from dashboard.backend.lifecycle_ledger import record_lifecycle_event

            canon = (
                "CLOSED_WIN" if new_status == "TARGET_HIT"
                else "CLOSED_LOSS" if new_status == "SL_HIT"
                else "CLOSED_EXIT"
            )
            record_lifecycle_event(
                row["symbol"], canon,
                prev_state="ENTRY_ACTIVE", source="close_position",
                position_id=position_id, user_id=user["sub"],
                planned_entry=entry, stop_loss=sl,
                cmp_at_event=exit_price, rr_planned=pnl_r,
                details={"exit_reason": exit_reason, "raw_status": new_status},
            )
        except Exception:
            pass
        return {
            "ok": True,
            "id": position_id,
            "symbol": row["symbol"],
            "status": new_status,
            "exit_price": exit_price,
            "pnl_r": pnl_r,
        }
    finally:
        conn.close()
