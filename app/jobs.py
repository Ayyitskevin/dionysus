"""SQLite-backed synchronous job facade.

The queue shape mirrors Mise so this can become a worker pool without changing the
product routes. MVP executes immediately for predictable local development.
"""

import json
import logging

from . import argus, db, generator, recipes

log = logging.getLogger("dionysus.jobs")


def enqueue_generate(campaign_id: int, recipe_id: int, *, argus_run_id: int | None = None) -> int:
    campaign = db.one("SELECT org_id FROM campaigns WHERE id=?", (campaign_id,))
    payload = {
        "campaign_id": campaign_id,
        "recipe_id": recipe_id,
        "argus_run_id": argus_run_id,
    }
    job_id = db.run("""INSERT INTO jobs (kind, payload, org_id)
                       VALUES (?,?,?)""",
                    ("generate_pack", json.dumps(payload),
                     campaign["org_id"] if campaign else None))
    execute(job_id)
    return job_id


def enqueue_regenerate(source_pack_id: int, feedback: str) -> int:
    source = db.one("SELECT org_id FROM content_packs WHERE id=?", (source_pack_id,))
    if not source:
        raise RuntimeError("source pack missing")
    payload = {"source_pack_id": source_pack_id, "feedback": feedback}
    job_id = db.run("""INSERT INTO jobs
                       (kind, payload, org_id, source_pack_id)
                       VALUES (?,?,?,?)""",
                    ("regenerate_pack", json.dumps(payload),
                     source["org_id"], source_pack_id))
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
        if job["kind"] == "generate_pack":
            result_pack_id = _execute_generate(payload)
        elif job["kind"] == "regenerate_pack":
            result_pack_id = _execute_regenerate(payload)
        else:
            raise RuntimeError(f"unknown job kind: {job['kind']}")
        db.run("""UPDATE jobs
                  SET status='done', result_pack_id=?, error=NULL,
                      completed_at=datetime('now'), updated_at=datetime('now')
                  WHERE id=?""", (result_pack_id, job_id))
    except Exception as exc:
        db.run("""UPDATE jobs
                  SET status='failed', error=?, updated_at=datetime('now')
                  WHERE id=?""", (str(exc)[:500], job_id))
        log.exception("job %s failed", job_id)


def _execute_generate(payload: dict) -> int:
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
    with db.tx() as con:
        cur = con.execute("""INSERT INTO content_packs
                             (org_id, campaign_id, recipe_id, title, body_json,
                              ai_model, ai_draft_original)
                             VALUES (?,?,?,?,?,?,?)""",
                          (org["id"], campaign["id"], recipe["id"], pack["headline"],
                           json.dumps(pack), pack["provenance"]["engine"], json.dumps(pack)))
        con.execute("UPDATE campaigns SET status='generated' WHERE id=?",
                    (campaign["id"],))
        return cur.lastrowid


def _execute_regenerate(payload: dict) -> int:
    source_pack_id = int(payload["source_pack_id"])
    feedback = str(payload.get("feedback") or "").strip()
    if len(feedback) < 4:
        raise RuntimeError("feedback is required")
    source = db.one("SELECT * FROM content_packs WHERE id=?", (source_pack_id,))
    if not source:
        raise RuntimeError("source pack missing")
    if source["archived_at"]:
        raise RuntimeError("source pack is archived")
    org = db.one("SELECT * FROM organizations WHERE id=?", (source["org_id"],))
    regenerated = generator.regenerate_with_feedback(org, source, feedback)
    engine = regenerated["provenance"]["engine"]
    with db.tx() as con:
        cur = con.execute("""INSERT INTO content_packs
                             (org_id, campaign_id, recipe_id, title, body_json,
                              ai_model, ai_draft_original, source_pack_id,
                              revision_note)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                          (org["id"], source["campaign_id"], source["recipe_id"],
                           regenerated["headline"], json.dumps(regenerated),
                           engine, json.dumps(regenerated), source["id"], feedback))
        return cur.lastrowid


def retry(job_id: int) -> None:
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise RuntimeError("job not found")
    if job["status"] != "failed":
        raise RuntimeError("only failed jobs can be retried")
    db.run("""UPDATE jobs
              SET status='queued', error=NULL, updated_at=datetime('now')
              WHERE id=?""", (job_id,))
    execute(job_id)


def _job(row) -> dict:
    job = dict(row)
    try:
        job["payload_data"] = json.loads(job["payload"] or "{}")
    except json.JSONDecodeError:
        job["payload_data"] = {}
    if job["kind"] == "regenerate_pack":
        title = job.get("source_pack_title") or f"pack #{job['source_pack_id']}"
        job["summary"] = f"Regenerate {title}"
    elif job["kind"] == "generate_pack":
        job["summary"] = "Generate campaign pack"
    else:
        job["summary"] = job["kind"]
    return job


def get_for_org(job_id: int, org_id: int) -> dict | None:
    row = db.one("""SELECT j.*, sp.title AS source_pack_title,
                           rp.title AS result_pack_title
                    FROM jobs j
                    LEFT JOIN content_packs sp ON sp.id=j.source_pack_id
                    LEFT JOIN content_packs rp ON rp.id=j.result_pack_id
                    WHERE j.id=? AND j.org_id=?""", (job_id, org_id))
    return _job(row) if row else None


def actionable_for_org(org_id: int, *, limit: int = 8) -> list[dict]:
    rows = db.all_("""SELECT j.*, sp.title AS source_pack_title,
                             rp.title AS result_pack_title
                      FROM jobs j
                      LEFT JOIN content_packs sp ON sp.id=j.source_pack_id
                      LEFT JOIN content_packs rp ON rp.id=j.result_pack_id
                      WHERE j.org_id=? AND j.status IN ('queued','running','failed')
                      ORDER BY j.created_at DESC, j.id DESC
                      LIMIT ?""", (org_id, limit))
    return [_job(row) for row in rows]


def recent_for_org(org_id: int, *, limit: int = 10) -> list[dict]:
    rows = db.all_("""SELECT j.*, sp.title AS source_pack_title,
                             rp.title AS result_pack_title
                      FROM jobs j
                      LEFT JOIN content_packs sp ON sp.id=j.source_pack_id
                      LEFT JOIN content_packs rp ON rp.id=j.result_pack_id
                      WHERE j.org_id=?
                      ORDER BY j.created_at DESC, j.id DESC
                      LIMIT ?""", (org_id, limit))
    return [_job(row) for row in rows]


def pending_count(org_id: int | None = None) -> int:
    if org_id is None:
        row = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")
    else:
        row = db.one("""SELECT COUNT(*) AS n FROM jobs
                        WHERE org_id=? AND status IN ('queued','running')""", (org_id,))
    return row["n"] if row else 0


def failed_count(org_id: int) -> int:
    row = db.one("SELECT COUNT(*) AS n FROM jobs WHERE org_id=? AND status='failed'",
                 (org_id,))
    return row["n"] if row else 0
