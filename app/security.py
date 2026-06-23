"""Signed workspace sessions, slugs, and service bearer checks."""

import re
import secrets
import string

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config

_BASE62 = string.ascii_letters + string.digits
_SLUG_SAFE = re.compile(r"[^a-z0-9]+")
WORKSPACE_COOKIE = "dionysus_workspace"


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


def has_workspace_access(request: Request, slug: str) -> bool:
    raw = request.cookies.get(WORKSPACE_COOKIE)
    return bool(raw) and unsign(raw) == f"workspace:{slug}"


def require_mise_token(request: Request) -> None:
    if not config.MISE_IMPORT_TOKEN:
        raise HTTPException(status_code=503, detail="mise import api disarmed")
    expected = f"Bearer {config.MISE_IMPORT_TOKEN}"
    if not secrets.compare_digest(request.headers.get("Authorization", ""), expected):
        raise HTTPException(status_code=401, detail="bad token")
