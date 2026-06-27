"""Service bearer-token check for the Mise content worker.

Identity/sessions/CSRF/passwords were removed when Dionysus converged to a
stateless worker — Mise owns identity. All worker APIs are gated by a single
shared bearer token.
"""

import secrets
import string

from fastapi import HTTPException, Request

from . import config, rate_limit

_BASE62 = string.ascii_letters + string.digits


def new_token(n: int = 18) -> str:
    return "".join(secrets.choice(_BASE62) for _ in range(n))


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
