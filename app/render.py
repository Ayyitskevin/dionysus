from pathlib import Path

from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=ROOT / "templates")
