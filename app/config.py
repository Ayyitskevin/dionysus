"""Dionysus configuration.

Studio mode (default): Mise operator service — print-pitch + argus-pack APIs only.
Set DIONYSUS_STUDIO_MODE=false to expose the legacy Platekit SaaS UI.
"""

import os
from pathlib import Path

# Mise admin feature — no public signup, Stripe, or workspace UI
STUDIO_MODE = os.environ.get("DIONYSUS_STUDIO_MODE", "true").lower() in ("1", "true", "yes")
STUDIO_OPERATOR_PLAN = os.environ.get("DIONYSUS_STUDIO_OPERATOR_PLAN", "restaurant_growth")

HOST = os.environ.get("DIONYSUS_HOST", "127.0.0.1")
PORT = int(os.environ.get("DIONYSUS_PORT", "8450"))
BASE_URL = os.environ.get("DIONYSUS_BASE_URL", f"http://localhost:{PORT}")

DATA_DIR = Path(os.environ.get("DIONYSUS_DATA_DIR", Path.cwd() / "data"))
DB_PATH = DATA_DIR / "dionysus.db"

SECRET_KEY = os.environ.get("DIONYSUS_SECRET_KEY", "dev-dionysus-secret")
ADMIN_PASSWORD = os.environ.get("DIONYSUS_ADMIN_PASSWORD", "dev")

MISE_IMPORT_TOKEN = os.environ.get("DIONYSUS_MISE_IMPORT_TOKEN", "")

# Argus vision metadata for pack enrichment (optional).
ARGUS_URL = os.environ.get("DIONYSUS_ARGUS_URL", "").rstrip("/")
ARGUS_API_TOKEN = os.environ.get("DIONYSUS_ARGUS_API_TOKEN", "")
ARGUS_TIMEOUT = int(os.environ.get("DIONYSUS_ARGUS_TIMEOUT", "15"))

JOB_WORKER_POLL_SECONDS = float(os.environ.get("DIONYSUS_WORKER_POLL_SECONDS", "2"))
JOB_STALE_SECONDS = int(os.environ.get("DIONYSUS_JOB_STALE_SECONDS", "900"))

STRIPE_SECRET_KEY = os.environ.get("DIONYSUS_STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("DIONYSUS_STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_RESTAURANT_STARTER = os.environ.get("DIONYSUS_STRIPE_PRICE_RESTAURANT_STARTER", "")
STRIPE_PRICE_RESTAURANT_GROWTH = os.environ.get("DIONYSUS_STRIPE_PRICE_RESTAURANT_GROWTH", "")
STRIPE_PRICE_PHOTOGRAPHER_STUDIO = os.environ.get("DIONYSUS_STRIPE_PRICE_PHOTOGRAPHER_STUDIO", "")


COOKIE_SECURE = os.environ.get("DIONYSUS_COOKIE_SECURE", "false").lower() in (
    "1", "true", "yes")
ENV = os.environ.get("DIONYSUS_ENV", "development")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
