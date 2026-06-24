from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import security

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=ROOT / "templates")


def money(cents: int | None) -> str:
    return f"${(cents or 0) / 100:,.0f}"


def csrf_token(request: Request) -> str:
    return security.csrf_token_for_request(request)


def csrf_input(request: Request) -> Markup:
    token = escape(csrf_token(request))
    return Markup(f'<input type="hidden" name="{security.CSRF_FIELD}" value="{token}">')


templates.env.filters["money"] = money
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["csrf_input"] = csrf_input
