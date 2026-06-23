"""Inbound Mise event hooks — Argus run completion → keyword-enriched pack drafts."""

from __future__ import annotations

import logging

from . import billing, db, jobs, plans

log = logging.getLogger("dionysus.mise_hook")


def default_recipe_slug(org: dict) -> str:
    sub = billing.checkout_state(org)
    if org["audience"] == "photographer":
        return "photographer-upsell"
    if sub["plan"] == "restaurant_starter":
        return "menu-launch"
    return "monthly-retainer"


def _campaign_title(*, mise_gallery_id: int | None, gallery_title: str | None,
                    argus_run_id: int) -> str:
    if mise_gallery_id:
        base = f"Mise gallery {mise_gallery_id}"
        if gallery_title:
            return f"{base}: {gallery_title}"
        return base
    if gallery_title:
        return gallery_title
    return f"Argus run {argus_run_id}"


def generate_from_argus(
    org: dict,
    *,
    argus_run_id: int,
    mise_gallery_id: int | None = None,
    gallery_title: str | None = None,
    recipe_slug: str | None = None,
) -> dict:
    """Create a campaign + draft pack enriched from one Argus run."""
    slug = recipe_slug or default_recipe_slug(org)
    recipe = db.one("SELECT * FROM content_recipes WHERE slug=? AND active=1", (slug,))
    if not recipe:
        raise ValueError(f"recipe {slug} not found")

    sub = billing.checkout_state(org)
    if not plans.allowed_recipe(sub["plan"], recipe["slug"]):
        raise ValueError(f"plan {sub['plan']} does not allow recipe {slug}")

    title = _campaign_title(
        mise_gallery_id=mise_gallery_id,
        gallery_title=(gallery_title or "").strip() or None,
        argus_run_id=argus_run_id,
    )
    campaign_id = db.run(
        """INSERT INTO campaigns (org_id, title, goal, status)
           VALUES (?,?,?,?)""",
        (org["id"], title, "Turn this shoot into reusable marketing", "draft"),
    )
    job_id = jobs.enqueue_generate(campaign_id, recipe["id"], argus_run_id=argus_run_id)
    pack = db.one(
        """SELECT id, title, body_json FROM content_packs
           WHERE campaign_id=? ORDER BY id DESC LIMIT 1""",
        (campaign_id,),
    )
    log.info(
        "mise argus hook org=%s gallery=%s run=%s -> campaign=%s pack=%s",
        org["slug"], mise_gallery_id, argus_run_id, campaign_id, pack["id"] if pack else None,
    )
    return {
        "campaign_id": campaign_id,
        "recipe_slug": recipe["slug"],
        "job_id": job_id,
        "pack_id": pack["id"] if pack else None,
        "pack_title": pack["title"] if pack else None,
    }