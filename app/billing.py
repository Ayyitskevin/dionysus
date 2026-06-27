"""Local plan / subscription state (no external payment processor).

The Stripe integration was removed when Dionysus converged to a stateless
content worker. This module now only tracks the local plan a workspace is on.
The ``subscriptions`` table is retained for the remaining SaaS UI until that
layer is stripped (see RETIRE.md, phase 2).
"""

from . import config, db, plans


def sync_trial_subscription(org_id: int, plan: str) -> None:
    db.run("""INSERT INTO subscriptions (org_id, plan, status)
              VALUES (?,?, 'trialing')
              ON CONFLICT(org_id) DO UPDATE SET plan=excluded.plan,
                status=CASE WHEN subscriptions.status='none' THEN 'trialing'
                            ELSE subscriptions.status END,
                updated_at=datetime('now')""", (org_id, plan))


def checkout_state(org) -> dict:
    if config.STUDIO_MODE:
        plan = config.STUDIO_OPERATOR_PLAN
        return {
            "plan": plan,
            "status": "active",
            "plan_meta": plans.PLANS.get(plan, plans.PLANS["restaurant_growth"]),
        }
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    plan = sub["plan"] if sub else org["plan"]
    return {
        "plan": plan,
        "status": sub["status"] if sub else "none",
        "plan_meta": plans.PLANS.get(plan, plans.PLANS["restaurant_starter"]),
    }
