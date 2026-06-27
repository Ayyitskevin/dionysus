"""Inbound Mise event hooks — Argus run completion → keyword-enriched pack drafts."""

from __future__ import annotations

import logging
import sqlite3

from . import billing, config, db, jobs, plans

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


def _ensure_campaign(
    org: dict,
    *,
    argus_run_id: int,
    mise_gallery_id: int | None,
    gallery_title: str | None,
    correlation_id: str | None,
) -> tuple[int, bool]:
    """Return (campaign_id, created). One campaign per (org, argus_run_id), so a
    retry of the same Argus run reuses it instead of drafting a duplicate."""
    existing = db.one(
        "SELECT id FROM campaigns WHERE org_id=? AND argus_run_id=? ORDER BY id LIMIT 1",
        (org["id"], argus_run_id))
    if existing:
        return int(existing["id"]), False
    title = _campaign_title(
        mise_gallery_id=mise_gallery_id,
        gallery_title=(gallery_title or "").strip() or None,
        argus_run_id=argus_run_id,
    )
    try:
        campaign_id = db.run(
            """INSERT INTO campaigns
               (org_id, title, goal, status, argus_run_id, correlation_id)
               VALUES (?,?,?,?,?,?)""",
            (org["id"], title, "Turn this shoot into reusable marketing", "draft",
             argus_run_id, correlation_id))
        return campaign_id, True
    except sqlite3.IntegrityError:  # concurrent first call won the unique index
        existing = db.one(
            "SELECT id FROM campaigns WHERE org_id=? AND argus_run_id=? ORDER BY id LIMIT 1",
            (org["id"], argus_run_id))
        if existing:
            return int(existing["id"]), False
        raise


def _hook_result(campaign_id: int, recipe: dict, job: dict,
                 correlation_id: str | None) -> dict:
    result_pack_id = job["result_pack_id"]
    pack_title = job.get("result_pack_title")
    if result_pack_id and not pack_title:
        row = db.one("SELECT title FROM content_packs WHERE id=?", (result_pack_id,))
        pack_title = row["title"] if row else None
    result = {
        "campaign_id": campaign_id,
        "recipe_slug": recipe["slug"],
        "job_id": job["id"],
        "job_status": job["status"],
        "pack_id": result_pack_id,
        "pack_title": pack_title,
    }
    if correlation_id:
        result["correlation_id"] = correlation_id
    return result


def generate_from_argus(
    org: dict,
    *,
    argus_run_id: int,
    mise_gallery_id: int | None = None,
    gallery_title: str | None = None,
    recipe_slug: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """Create a campaign and queue a draft pack enriched from one Argus run.

    Idempotent per (org, argus_run_id): a retry — before or after the job
    completes — returns the same campaign/job/pack rather than drafting a
    duplicate. Because a completed draft is returned untouched (never
    overwritten), any human edits on it are inherently preserved.
    """
    slug = recipe_slug or default_recipe_slug(org)
    recipe = db.one("SELECT * FROM content_recipes WHERE slug=? AND active=1", (slug,))
    if not recipe:
        raise ValueError(f"recipe {slug} not found")

    if not config.STUDIO_MODE:
        sub = billing.checkout_state(org)
        if not plans.allowed_recipe(sub["plan"], recipe["slug"]):
            raise ValueError(f"plan {sub['plan']} does not allow recipe {slug}")

    campaign_id, created = _ensure_campaign(
        org,
        argus_run_id=argus_run_id,
        mise_gallery_id=mise_gallery_id,
        gallery_title=gallery_title,
        correlation_id=correlation_id,
    )
    if not created:
        reusable = jobs.reusable_generate_job(
            campaign_id, recipe["id"], argus_run_id=argus_run_id)
        if reusable:
            log.info("mise argus hook idempotent org=%s run=%s campaign=%s job=%s",
                     org["slug"], argus_run_id, campaign_id, reusable["id"])
            return _hook_result(campaign_id, recipe, reusable, correlation_id)

    job_id = jobs.enqueue_generate(campaign_id, recipe["id"], argus_run_id=argus_run_id)
    job = jobs.get(job_id)
    log.info(
        "mise argus hook org=%s gallery=%s run=%s -> campaign=%s job=%s",
        org["slug"], mise_gallery_id, argus_run_id, campaign_id, job_id,
    )
    return _hook_result(campaign_id, recipe, job, correlation_id)
