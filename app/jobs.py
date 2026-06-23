"""SQLite-backed synchronous job facade.

The queue shape mirrors Mise so this can become a worker pool without changing the
product routes. MVP executes immediately for predictable local development.
"""

import json
import logging

from . import argus, db, generator, recipes

log = logging.getLogger("dionysus.jobs")


def enqueue_generate(campaign_id: int, recipe_id: int, *, argus_run_id: int | None = None) -> int:
    job_id = db.run("INSERT INTO jobs (kind, payload) VALUES (?,?)",
                    ("generate_pack", json.dumps({
                        "campaign_id": campaign_id,
                        "recipe_id": recipe_id,
                        "argus_run_id": argus_run_id,
                    })))
    execute(job_id)
    return job_id


def execute(job_id: int) -> None:
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job or job["status"] != "queued":
        return
    db.run("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=datetime('now') "
           "WHERE id=?", (job_id,))
    try:
        payload = json.loads(job["payload"])
        campaign = db.one("SELECT * FROM campaigns WHERE id=?", (payload["campaign_id"],))
        recipe = recipes.by_id(payload["recipe_id"])
        if not campaign or not recipe:
            raise RuntimeError("campaign or recipe missing")
        org = db.one("SELECT * FROM organizations WHERE id=?", (campaign["org_id"],))
        argus_ctx = None
        run_id = payload.get("argus_run_id")
        if run_id:
            try:
                argus_ctx = argus.fetch_run_context(int(run_id))
            except argus.ArgusError:
                log.warning("argus enrichment skipped for run %s", run_id, exc_info=True)
        pack = generator.build_pack(org, campaign, recipe, argus_context=argus_ctx)
        db.run("""INSERT INTO content_packs
                  (org_id, campaign_id, recipe_id, title, body_json, ai_model, ai_draft_original)
                  VALUES (?,?,?,?,?,?,?)""",
               (org["id"], campaign["id"], recipe["id"], pack["headline"],
                json.dumps(pack), pack["provenance"]["engine"], json.dumps(pack)))
        db.run("UPDATE campaigns SET status='generated' WHERE id=?", (campaign["id"],))
        db.run("UPDATE jobs SET status='done', error=NULL, updated_at=datetime('now') "
               "WHERE id=?", (job_id,))
    except Exception as exc:
        db.run("UPDATE jobs SET status='failed', error=?, updated_at=datetime('now') "
               "WHERE id=?", (str(exc)[:500], job_id))
        log.exception("job %s failed", job_id)


def pending_count() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")
    return row["n"] if row else 0
