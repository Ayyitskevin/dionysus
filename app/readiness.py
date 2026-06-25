"""Production / studio readiness checks."""

from urllib.parse import urlparse

from . import config

PLACEHOLDER_SECRETS = {"", "change-me", "dev", "dev-dionysus-secret"}


def studio_checks() -> list[dict]:
    return [
        {
            "key": "studio_mode",
            "ok": config.STUDIO_MODE,
            "detail": "Dionysus runs as Mise operator service (no SaaS sales)",
        },
        {
            "key": "mise_bridge",
            "ok": bool(config.MISE_IMPORT_TOKEN),
            "detail": "DIONYSUS_MISE_IMPORT_TOKEN arms print-pitch and argus-pack",
        },
    ]


def production_checks() -> list[dict]:
    parsed = urlparse(config.BASE_URL)
    stripe_prices = {
        "restaurant_starter": config.STRIPE_PRICE_RESTAURANT_STARTER,
        "restaurant_growth": config.STRIPE_PRICE_RESTAURANT_GROWTH,
        "photographer_studio": config.STRIPE_PRICE_PHOTOGRAPHER_STUDIO,
    }
    return [
        {
            "key": "secret_key",
            "ok": config.SECRET_KEY not in PLACEHOLDER_SECRETS,
            "detail": "DIONYSUS_SECRET_KEY is set to a non-placeholder value",
        },
        {
            "key": "base_url",
            "ok": parsed.scheme == "https" and parsed.netloc and "localhost" not in parsed.netloc,
            "detail": "DIONYSUS_BASE_URL is a public HTTPS origin",
        },
        {
            "key": "cookie_secure",
            "ok": config.COOKIE_SECURE,
            "detail": "DIONYSUS_COOKIE_SECURE=true for production cookies",
        },
        {
            "key": "stripe_secret",
            "ok": bool(config.STRIPE_SECRET_KEY and config.STRIPE_SECRET_KEY.startswith("sk_")),
            "detail": "DIONYSUS_STRIPE_SECRET_KEY is present",
        },
        {
            "key": "stripe_webhook",
            "ok": bool(config.STRIPE_WEBHOOK_SECRET and config.STRIPE_WEBHOOK_SECRET.startswith("whsec_")),
            "detail": "DIONYSUS_STRIPE_WEBHOOK_SECRET is present",
        },
        {
            "key": "stripe_prices",
            "ok": all(v.startswith("price_") for v in stripe_prices.values()),
            "detail": "all Stripe plan price IDs are configured",
        },
        {
            "key": "mise_bridge",
            "ok": bool(config.MISE_IMPORT_TOKEN),
            "detail": "DIONYSUS_MISE_IMPORT_TOKEN is set for the Mise bridge",
        },
    ]


def checks() -> list[dict]:
    if config.STUDIO_MODE:
        return studio_checks()
    return production_checks()


def summary() -> dict:
    items = checks()
    return {
        "ready": all(item["ok"] for item in items),
        "studio_mode": config.STUDIO_MODE,
        "checks": items,
    }