"""Small SQLite-backed rate limiter for public/auth entry points."""

from __future__ import annotations

from fastapi import Request

from . import db

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
