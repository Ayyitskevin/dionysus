"""Signed sessions, password hashing, slugs, and service bearer checks."""

import hashlib
import hmac
import os
import re
import secrets
import string

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, rate_limit

_BASE62 = string.ascii_letters + string.digits
_SLUG_SAFE = re.compile(r"[^a-z0-9]+")
WORKSPACE_COOKIE = "dionysus_workspace"
USER_COOKIE = "dionysus_user"
CSRF_COOKIE = "dionysus_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.SECRET_KEY, salt="dionysus")


def new_token(n: int = 18) -> str:
    return "".join(secrets.choice(_BASE62) for _ in range(n))


def slugify(value: str) -> str:
    slug = _SLUG_SAFE.sub("-", value.lower()).strip("-")
    return slug[:48] or new_token(8).lower()


def sign(value: str) -> str:
    return _serializer().dumps(value)


def unsign(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=60 * 60 * 24 * 90)
    except BadSignature:
        return None


def workspace_cookie(slug: str) -> str:
    return sign(f"workspace:{slug}")


def user_cookie(user_id: int) -> str:
    return sign(f"user:{user_id}")


def _valid_csrf_token(token: str | None) -> bool:
    if not token:
        return False
    unsigned = unsign(token)
    return bool(unsigned and unsigned.startswith("csrf:"))


def csrf_token_for_request(request: Request) -> str:
    token = getattr(request.state, "csrf_token", "")
    if _valid_csrf_token(token):
        return token
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if _valid_csrf_token(cookie_token):
        token = cookie_token
    else:
        token = sign(f"csrf:{new_token(32)}")
    request.state.csrf_token = token
    return token


def verify_csrf(request: Request, submitted_token: str | None) -> bool:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not _valid_csrf_token(cookie_token) or not _valid_csrf_token(submitted_token):
        return False
    return secrets.compare_digest(cookie_token, submitted_token or "")


def user_id_from_request(request: Request) -> int | None:
    raw = request.cookies.get(USER_COOKIE)
    token = unsign(raw) if raw else None
    if not token or not token.startswith("user:"):
        return None
    try:
        return int(token.split(":", 1)[1])
    except ValueError:
        return None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def require_mise_token(request: Request) -> None:
    if not config.MISE_IMPORT_TOKEN:
        raise HTTPException(status_code=503, detail="mise import api disarmed")
    header = request.headers.get("Authorization", "")
    expected = f"Bearer {config.MISE_IMPORT_TOKEN}"
    if not secrets.compare_digest(header, expected):
        try:
            rate_limit.check(
                "mise_token:ip",
                rate_limit.client_ip(request),
                limit=rate_limit.MISE_TOKEN_LIMIT,
                window_seconds=rate_limit.MISE_TOKEN_WINDOW_SECONDS,
            )
        except rate_limit.RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail="too many attempts") from exc
        raise HTTPException(status_code=401, detail="bad token")
