"""Subscription plan definitions and feature gates."""

PLANS = {
    "restaurant_starter": {
        "name": "Restaurant Starter",
        "price_cents": 4900,
        "audience": "restaurant",
        "pack_limit": 3,
        "recipes": {"menu-launch", "monthly-retainer", "delivery-app-refresh"},
        "features": ["12 captions/month", "3 campaign briefs", "delivery-app copy"],
        "for": "Owners who need weekly posts from existing shoots",
    },
    "restaurant_growth": {
        "name": "Restaurant Growth",
        "price_cents": 14900,
        "audience": "restaurant",
        "pack_limit": None,
        "recipes": {"menu-launch", "monthly-retainer", "delivery-app-refresh",
                    "press-seasonal"},
        "features": ["Unlimited draft packs", "menu-launch calendar", "license prompts"],
        "for": "Operators with seasonal menus and paid campaigns",
    },
    "photographer_studio": {
        "name": "Photographer Studio",
        "price_cents": 9900,
        "audience": "photographer",
        "pack_limit": 10,
        "recipes": {"monthly-retainer", "photographer-upsell", "press-seasonal"},
        "features": ["Client intake workspaces", "shot-list packs", "upsell scripts"],
        "for": "Food photographers selling retainers and add-ons",
    },
}


def all_plans() -> list[dict]:
    return [{"key": key, **value} for key, value in PLANS.items()]


def normalize_plan(plan: str, audience: str) -> str:
    if plan in PLANS:
        return plan
    if audience == "photographer":
        return "photographer_studio"
    if plan == "growth":
        return "restaurant_growth"
    return "restaurant_starter"


def allowed_recipe(plan: str, recipe_slug: str) -> bool:
    p = PLANS.get(plan, PLANS["restaurant_starter"])
    return recipe_slug in p["recipes"]


def pack_limit(plan: str) -> int | None:
    return PLANS.get(plan, PLANS["restaurant_starter"])["pack_limit"]
