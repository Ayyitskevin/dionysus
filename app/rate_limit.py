"""Small SQLite-backed rate limiter for public/auth entry points."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from . import config, db

AUTH_WINDOW_SECONDS = 15 * 60
LOGIN_SUBJECT_LIMIT = 5
LOGIN_IP_LIMIT = 25
SIGNUP_SUBJECT_LIMIT = 2
SIGNUP_IP_LIMIT = 10
INVITE_SUBJECT_LIMIT = 5
INVITE_IP_LIMIT = 20
MISE_TOKEN_LIMIT = 10
MISE_TOKEN_WINDOW_SECONDS = 15 * 60


class RateLimitExceeded(Exception):
    """Raised when a subject has too many recent attempts."""


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def identity(*parts: str) -> str:
    return ":".join((part or "-").strip().lower() or "-" for part in parts)


def bucket_fingerprint(action: str, identity_value: str) -> str:
    secret = config.SECRET_KEY.encode("utf-8")
    normalized = f"{action.strip().lower()}\0{identity(identity_value)}"
    digest = hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"rl_{digest[:12]}"


def recent_summary(
    *,
    window_seconds: int = AUTH_WINDOW_SECONDS,
    limit: int = 20,
    action: str = "",
) -> list[dict]:
    if window_seconds <= 0 or limit <= 0:
        return []
    filters = ["datetime(created_at) >= datetime('now', ?)"]
    params: list[object] = [f"-{int(window_seconds)} seconds"]
    action = action.strip().lower()
    if action:
        filters.append("action=?")
        params.append(action)
    params.append(int(limit))
    rows = db.all_(
        f"""SELECT action, identity, COUNT(*) AS attempts,
                  MIN(created_at) AS first_seen,
                  MAX(created_at) AS last_seen
           FROM rate_limit_events
           WHERE {' AND '.join(filters)}
           GROUP BY action, identity
           ORDER BY attempts DESC, datetime(last_seen) DESC, action ASC
           LIMIT ?""",
        tuple(params),
    )
    return [
        {
            "action": row["action"],
            "attempts": row["attempts"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "bucket": bucket_fingerprint(row["action"], row["identity"]),
        }
        for row in rows
    ]


def check(action: str, identity_value: str, *, limit: int, window_seconds: int) -> None:
    if limit <= 0 or window_seconds <= 0:
        return
    action = action.strip().lower()
    identity_value = identity(identity_value)
    window = f"-{int(window_seconds)} seconds"
    blocked = False
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            """DELETE FROM rate_limit_events
               WHERE action=? AND identity=?
                 AND datetime(created_at) < datetime('now', ?)""",
            (action, identity_value, window),
        )
        row = con.execute(
            """SELECT COUNT(*) AS n
               FROM rate_limit_events
               WHERE action=? AND identity=?
                 AND datetime(created_at) >= datetime('now', ?)""",
            (action, identity_value, window),
        ).fetchone()
        if row and int(row["n"]) >= limit:
            blocked = True
        else:
            con.execute(
                "INSERT INTO rate_limit_events (action, identity) VALUES (?, ?)",
                (action, identity_value),
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    if blocked:
        raise RateLimitExceeded(f"too many attempts for {action}")
