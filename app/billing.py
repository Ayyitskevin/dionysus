"""Stripe subscription scaffolding."""

from . import config, db, plans


def price_id(plan: str) -> str:
    return {
        "restaurant_starter": config.STRIPE_PRICE_RESTAURANT_STARTER,
        "restaurant_growth": config.STRIPE_PRICE_RESTAURANT_GROWTH,
        "photographer_studio": config.STRIPE_PRICE_PHOTOGRAPHER_STUDIO,
    }.get(plan, "")


def configured_for(plan: str) -> bool:
    return bool(config.STRIPE_SECRET_KEY and price_id(plan))


def sync_trial_subscription(org_id: int, plan: str) -> None:
    db.run("""INSERT INTO subscriptions (org_id, plan, status)
              VALUES (?,?, 'trialing')
              ON CONFLICT(org_id) DO UPDATE SET plan=excluded.plan,
                status=CASE WHEN subscriptions.status='none' THEN 'trialing'
                            ELSE subscriptions.status END,
                updated_at=datetime('now')""", (org_id, plan))


def checkout_state(org) -> dict:
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    plan = sub["plan"] if sub else org["plan"]
    return {
        "configured": configured_for(plan),
        "plan": plan,
        "price_id": price_id(plan),
        "status": sub["status"] if sub else "none",
        "plan_meta": plans.PLANS.get(plan, plans.PLANS["restaurant_starter"]),
    }
