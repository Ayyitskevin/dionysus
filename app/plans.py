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
    if plan in PLANS and PLANS[plan]["audience"] == audience:
        return plan
    if audience == "photographer":
        return "photographer_studio"
    if plan in {"growth", "restaurant_growth"}:
        return "restaurant_growth"
    return "restaurant_starter"


def allowed_recipe(plan: str, recipe_slug: str) -> bool:
    p = PLANS.get(plan, PLANS["restaurant_starter"])
    return recipe_slug in p["recipes"]


def pack_limit(plan: str) -> int | None:
    return PLANS.get(plan, PLANS["restaurant_starter"])["pack_limit"]


def upgrade_plan_for_recipe(audience: str, current_plan: str, recipe_slug: str) -> str | None:
    """Smallest plan in the same audience that unlocks `recipe_slug`."""
    current = PLANS.get(current_plan, PLANS["restaurant_starter"])
    candidates = []
    for key, plan in PLANS.items():
        if plan["audience"] != audience:
            continue
        if recipe_slug not in plan["recipes"]:
            continue
        if key == current_plan:
            return None
        candidates.append((plan["price_cents"], key))
    if not candidates:
        return None
    candidates.sort()
    target = candidates[0][1]
    return target if PLANS[target]["price_cents"] > current["price_cents"] else None


def upgrade_plan_for_limit(audience: str, current_plan: str) -> str | None:
    current = PLANS.get(current_plan, PLANS["restaurant_starter"])
    current_limit = current["pack_limit"]
    candidates = []
    for key, plan in PLANS.items():
        if plan["audience"] != audience or key == current_plan:
            continue
        limit = plan["pack_limit"]
        better_limit = current_limit is not None and (
            limit is None or limit > current_limit)
        if better_limit and plan["price_cents"] > current["price_cents"]:
            candidates.append((plan["price_cents"], key))
    candidates.sort()
    return candidates[0][1] if candidates else None
