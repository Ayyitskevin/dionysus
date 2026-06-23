"""Stripe checkout and subscription sync."""

from fastapi import HTTPException, Request

from . import config, db, plans


def price_id(plan: str) -> str:
    return {
        "restaurant_starter": config.STRIPE_PRICE_RESTAURANT_STARTER,
        "restaurant_growth": config.STRIPE_PRICE_RESTAURANT_GROWTH,
        "photographer_studio": config.STRIPE_PRICE_PHOTOGRAPHER_STUDIO,
    }.get(plan, "")


def configured_for(plan: str) -> bool:
    return bool(config.STRIPE_SECRET_KEY and price_id(plan))


def _stripe():
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover - exercised by deployment config
        raise HTTPException(status_code=503, detail="Stripe dependency is not installed") from exc
    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


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


def create_checkout_session(org, success_url: str, cancel_url: str) -> str:
    state = checkout_state(org)
    plan = state["plan"]
    if not configured_for(plan):
        raise HTTPException(status_code=503, detail="Stripe checkout is not configured")
    stripe = _stripe()
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    customer = sub["stripe_customer_id"] if sub and sub["stripe_customer_id"] else None
    kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price_id(plan), "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(org["id"]),
        "metadata": {"org_id": str(org["id"]), "plan": plan},
        "subscription_data": {"metadata": {"org_id": str(org["id"]), "plan": plan}},
    }
    if customer:
        kwargs["customer"] = customer
    else:
        kwargs["customer_email"] = org["email"]
    session = stripe.checkout.Session.create(**kwargs)
    url = getattr(session, "url", None) or session.get("url")
    if not url:
        raise HTTPException(status_code=502, detail="Stripe did not return a checkout URL")
    return url


def _event_object(event: dict):
    return event.get("data", {}).get("object", {})


def _metadata(obj) -> dict:
    return dict(obj.get("metadata") or {})


def _org_id_from(obj) -> int | None:
    meta = _metadata(obj)
    raw = meta.get("org_id") or obj.get("client_reference_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _plan_from(obj, fallback: str = "restaurant_starter") -> str:
    return _metadata(obj).get("plan") or fallback


def upsert_subscription(org_id: int, *, plan: str, status: str,
                        customer_id: str | None = None,
                        subscription_id: str | None = None,
                        current_period_end: str | None = None) -> None:
    db.run("""INSERT INTO subscriptions
              (org_id, plan, status, stripe_customer_id, stripe_subscription_id,
               current_period_end, updated_at)
              VALUES (?,?,?,?,?,?,datetime('now'))
              ON CONFLICT(org_id) DO UPDATE SET
                plan=excluded.plan,
                status=excluded.status,
                stripe_customer_id=COALESCE(excluded.stripe_customer_id,
                                            subscriptions.stripe_customer_id),
                stripe_subscription_id=COALESCE(excluded.stripe_subscription_id,
                                                subscriptions.stripe_subscription_id),
                current_period_end=COALESCE(excluded.current_period_end,
                                            subscriptions.current_period_end),
                updated_at=datetime('now')""",
           (org_id, plan, status, customer_id, subscription_id, current_period_end))


def handle_event(event: dict) -> dict:
    typ = event.get("type")
    obj = _event_object(event)

    if typ == "checkout.session.completed":
        org_id = _org_id_from(obj)
        if not org_id:
            return {"ok": True, "ignored": "missing org_id"}
        upsert_subscription(
            org_id,
            plan=_plan_from(obj),
            status="active",
            customer_id=obj.get("customer"),
            subscription_id=obj.get("subscription"),
        )
        return {"ok": True, "handled": typ}

    if typ in {"customer.subscription.created", "customer.subscription.updated",
               "customer.subscription.deleted"}:
        org_id = _org_id_from(obj)
        if not org_id:
            return {"ok": True, "ignored": "missing org_id"}
        stripe_status = obj.get("status") or "active"
        status = "canceled" if typ == "customer.subscription.deleted" else stripe_status
        if status not in {"trialing", "active", "past_due", "canceled"}:
            status = "past_due"
        period_end = obj.get("current_period_end")
        upsert_subscription(
            org_id,
            plan=_plan_from(obj),
            status=status,
            customer_id=obj.get("customer"),
            subscription_id=obj.get("id"),
            current_period_end=str(period_end) if period_end else None,
        )
        return {"ok": True, "handled": typ}

    return {"ok": True, "ignored": typ}


async def construct_webhook_event(request: Request) -> dict:
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not config.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Stripe webhook is not configured")
    stripe = _stripe()
    try:
        return stripe.Webhook.construct_event(payload, sig, config.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid Stripe webhook") from exc
