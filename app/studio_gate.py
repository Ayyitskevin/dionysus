"""Studio mode — Mise operator service (no public signup, billing, or workspace UI)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import config

_BLOCKED_PREFIXES = (
    "/w/",
    "/invite/",
    "/share/",
    "/stripe/",
)
_BLOCKED_EXACT = frozenset({
    "/login",
    "/logout",
    "/pricing",
    "/signup",
})


def is_saas_path(path: str) -> bool:
    if path in _BLOCKED_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _BLOCKED_PREFIXES)


async def studio_mode_middleware(request: Request, call_next):
    if not config.STUDIO_MODE:
        return await call_next(request)
    path = request.url.path
    if path == "/signup" or (request.method == "POST" and path == "/" and "signup" in (
        request.headers.get("content-type") or ""
    )):
        return JSONResponse({"detail": "studio mode — use Mise admin"}, status_code=404)
    if is_saas_path(path):
        if "text/html" in request.headers.get("accept", ""):
            return PlainTextResponse("studio mode — use Mise admin", status_code=404)
        return JSONResponse({"detail": "studio mode — use Mise admin"}, status_code=404)
    return await call_next(request)