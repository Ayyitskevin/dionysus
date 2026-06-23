from pathlib import Path

from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=ROOT / "templates")


def money(cents: int | None) -> str:
    return f"${(cents or 0) / 100:,.0f}"


templates.env.filters["money"] = money
