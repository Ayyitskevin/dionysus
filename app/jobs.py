"""SQLite-backed asynchronous job queue.

Web routes enqueue durable jobs. A CLI worker claims queued jobs and executes
them outside the request path.
"""

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any

from . import argus, audit, config, db, generator, recipes

log = logging.getLogger("dionysus.jobs")

PACK_JOB_KINDS = ("generate_pack", "regenerate_pack")
ACTIVE_STATUSES = ("queued", "running")


def _normalize_feedback(feedback: str) -> str:
    return " ".join(feedback.strip().lower().split())


def _generate_idempotency_key(
    campaign_id: int,
    recipe_id: int,
    argus_run_id: int | None,
) -> str:
    run_part = "" if argus_run_id is None else str(argus_run_id)
    return f"generate_pack:{campaign_id}:{recipe_id}:{run_part}"


def _regenerate_idempotency_key(source_pack_id: int, feedback: str) -> str:
    digest = hashlib.sha256(_normalize_feedback(feedback).encode()).hexdigest()
    return f"regenerate_pack:{source_pack_id}:{digest}"


def _active_job_id(idempotency_key: str) -> int | None:
    row = db.one("""SELECT id FROM jobs
                    WHERE idempotency_key=?
                      AND status IN ('queued','running')
                    ORDER BY id
                    LIMIT 1""", (idempotency_key,))
    return int(row["id"]) if row else None


def _active_legacy_job_id(kind: str, expected_payload: dict[str, Any]) -> int | None:
    rows = db.all_("""SELECT id, payload FROM jobs
                    WHERE kind=?
                      AND idempotency_key IS NULL
                      AND status IN ('queued','running')
                    ORDER BY id""", (kind,))
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        matches = True
        for key, value in expected_payload.items():
            if key == "feedback":
                if _normalize_feedback(str(payload.get(key) or "")) != _normalize_feedback(str(value)):
                    matches = False
                    break
            elif payload.get(key) != value:
                matches = False
                break
        if matches:
            return int(row["id"])
    return None


def active_generate_job_id(
    campaign_id: int,
    recipe_id: int,
    *,
    argus_run_id: int | None = None,
) -> int | None:
    key = _generate_idempotency_key(campaign_id, recipe_id, argus_run_id)
    return _active_job_id(key) or _active_legacy_job_id(
        "generate_pack",
        {
            "campaign_id": campaign_id,
            "recipe_id": recipe_id,
            "argus_run_id": argus_run_id,
        },
    )


def active_regenerate_job_id(source_pack_id: int, feedback: str) -> int | None:
    key = _regenerate_idempotency_key(source_pack_id, feedback)
    return _active_job_id(key) or _active_legacy_job_id(
        "regenerate_pack",
        {
            "source_pack_id": source_pack_id,
            "feedback": feedback,
        },
    )


def _insert_idempotent_job(
    *,
    kind: str,
    payload: dict[str, Any],
    org_id: int,
    idempotency_key: str,
    source_pack_id: int | None = None,
) -> int:
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """SELECT id FROM jobs
               WHERE idempotency_key=?
                 AND status IN ('queued','running')
               ORDER BY id
               LIMIT 1""",
            (idempotency_key,),
        ).fetchone()
        if existing:
            con.commit()
            return int(existing["id"])
        try:
            cur = con.execute(
                """INSERT INTO jobs
                   (kind, payload, org_id, source_pack_id, idempotency_key)
                   VALUES (?,?,?,?,?)""",
                (kind, json.dumps(payload), org_id, source_pack_id, idempotency_key),
            )
        except sqlite3.IntegrityError:
            existing = con.execute(
                """SELECT id FROM jobs
                   WHERE idempotency_key=?
                     AND status IN ('queued','running')
                   ORDER BY id
                   LIMIT 1""",
                (idempotency_key,),
            ).fetchone()
            if existing:
                con.commit()
                return int(existing["id"])
            raise
        con.commit()
        return int(cur.lastrowid)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def enqueue_generate(campaign_id: int, recipe_id: int, *,
                     argus_run_id: int | None = None) -> int:
    campaign = db.one("SELECT org_id FROM campaigns WHERE id=?", (campaign_id,))
    if not campaign:
        raise RuntimeError("campaign missing")
    payload = {
        "campaign_id": campaign_id,
        "recipe_id": recipe_id,
        "argus_run_id": argus_run_id,
    }
    existing = active_generate_job_id(campaign_id, recipe_id, argus_run_id=argus_run_id)
    if existing is not None:
        return existing
    return _insert_idempotent_job(
        kind="generate_pack",
        payload=payload,
        org_id=campaign["org_id"],
        idempotency_key=_generate_idempotency_key(campaign_id, recipe_id, argus_run_id),
    )


def enqueue_regenerate(source_pack_id: int, feedback: str, *,
                       actor_user_id: int | None = None) -> int:
    source = db.one("SELECT org_id FROM content_packs WHERE id=?", (source_pack_id,))
    if not source:
        raise RuntimeError("source pack missing")
    payload: dict[str, Any] = {"source_pack_id": source_pack_id, "feedback": feedback}
    if actor_user_id is not None:
        payload["actor_user_id"] = actor_user_id
    existing = active_regenerate_job_id(source_pack_id, feedback)
    if existing is not None:
        return existing
    return _insert_idempotent_job(
        kind="regenerate_pack",
        payload=payload,
        org_id=source["org_id"],
        source_pack_id=source_pack_id,
        idempotency_key=_regenerate_idempotency_key(source_pack_id, feedback),
    )


def execute(job_id: int) -> dict | None:
    """Claim and execute one queued job by id."""
    job = _claim(job_id=job_id)
    if not job:
        return None
    return _finish(job)


def run_next() -> dict | None:
    """Claim and execute the oldest queued job."""
    job = _claim()
    if not job:
        return None
    return _finish(job)


def drain(*, limit: int | None = None) -> int:
    """Process queued jobs until empty or until limit jobs have run."""
    processed = 0
    while limit is None or processed < limit:
        if not run_next():
            break
        processed += 1
    return processed


def work(*, poll_seconds: float | None = None, limit: int | None = None) -> int:
    """Run worker loop. With a limit, drain up to that many jobs and return."""
    processed = 0
    poll = config.JOB_WORKER_POLL_SECONDS if poll_seconds is None else poll_seconds
    while True:
        requeue_stale_running()
        job = run_next()
        if job:
            processed += 1
            if limit is not None and processed >= limit:
                return processed
            continue
        if limit is not None:
            return processed
        time.sleep(max(poll, 0.1))


def requeue_stale_running(timeout_seconds: int | None = None) -> int:
    timeout = config.JOB_STALE_SECONDS if timeout_seconds is None else timeout_seconds
    if timeout <= 0:
        return 0
    con = db.connect()
    try:
        cur = con.execute("""UPDATE jobs
                             SET status='queued',
                                 error='worker lease expired before completion',
                                 updated_at=datetime('now')
                             WHERE status='running'
                               AND datetime(COALESCE(updated_at, created_at))
                                   < datetime('now', ?)""",
                          (f"-{int(timeout)} seconds",))
        con.commit()
        if cur.rowcount:
            log.warning("requeued %s stale running jobs", cur.rowcount)
        return cur.rowcount
    finally:
        con.close()


def retry(job_id: int) -> None:
    with db.tx() as con:
        job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise RuntimeError("job not found")
        if job["status"] != "failed":
            raise RuntimeError("only failed jobs can be retried")
        con.execute("""UPDATE jobs
                       SET status='queued', error=NULL, result_pack_id=NULL,
                           completed_at=NULL, updated_at=datetime('now')
                       WHERE id=?""", (job_id,))


def _claim(*, job_id: int | None = None):
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        if job_id is None:
            job = con.execute("""SELECT * FROM jobs
                                 WHERE status='queued'
                                 ORDER BY created_at, id
                                 LIMIT 1""").fetchone()
        else:
            job = con.execute("""SELECT * FROM jobs
                                 WHERE id=? AND status='queued'""",
                              (job_id,)).fetchone()
        if not job:
            con.commit()
            return None
        con.execute("""UPDATE jobs
                       SET status='running', attempts=attempts+1,
                           updated_at=datetime('now')
                       WHERE id=? AND status='queued'""", (job["id"],))
        claimed = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        con.commit()
        return claimed
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _finish(job) -> dict | None:
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
                  WHERE id=?""", (result_pack_id, job["id"]))
    except Exception as exc:
        db.run("""UPDATE jobs
                  SET status='failed', error=?, updated_at=datetime('now')
                  WHERE id=?""", (str(exc)[:500], job["id"]))
        log.exception("job %s failed", job["id"])
    return get(job["id"])


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
    enriched = generator.enrich_pack_with_model(
        pack, org=org, campaign=campaign, recipe=recipe, argus_context=argus_ctx)
    if enriched:
        pack = enriched
    ai_model = pack["provenance"].get("model") or pack["provenance"]["engine"]
    with db.tx() as con:
        cur = con.execute("""INSERT INTO content_packs
                             (org_id, campaign_id, recipe_id, title, body_json,
                              ai_model, ai_draft_original)
                             VALUES (?,?,?,?,?,?,?)""",
                          (org["id"], campaign["id"], recipe["id"], pack["headline"],
                           json.dumps(pack), ai_model, json.dumps(pack)))
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
        result_pack_id = cur.lastrowid
    audit.log_event(
        org["id"], "pack.regenerated",
        actor_user_id=payload.get("actor_user_id"), entity_type="content_pack",
        entity_id=result_pack_id,
        summary=f"Regenerated {source['title']} into a new draft.",
        details={
            "source_pack_id": source["id"],
            "source_status": source["status"],
            "pack_title": regenerated["headline"],
            "feedback": feedback,
        })
    return result_pack_id


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


def get(job_id: int) -> dict | None:
    row = db.one("""SELECT j.*, sp.title AS source_pack_title,
                           rp.title AS result_pack_title
                    FROM jobs j
                    LEFT JOIN content_packs sp ON sp.id=j.source_pack_id
                    LEFT JOIN content_packs rp ON rp.id=j.result_pack_id
                    WHERE j.id=?""", (job_id,))
    return _job(row) if row else None


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


def workspace_for_org(org_id: int, *, focus_job_id: int = 0,
                      limit: int = 8) -> list[dict]:
    rows = actionable_for_org(org_id, limit=limit)
    if not focus_job_id or any(job["id"] == focus_job_id for job in rows):
        return rows
    focused = get_for_org(focus_job_id, org_id)
    if not focused:
        return rows
    return [focused, *rows[:max(limit - 1, 0)]]


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


def _duration_label(seconds: int | None) -> str:
    if seconds is None:
        return "No backlog"
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return "<1m"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h" if hours else f"{days}d"


def queue_stats(org_id: int | None = None) -> dict:
    where_sql = "WHERE org_id=?" if org_id is not None else ""
    params: list[Any] = []
    stale_seconds = max(int(config.JOB_STALE_SECONDS), 0)
    if stale_seconds:
        stale_expr = """CASE
                            WHEN status='running'
                             AND datetime(COALESCE(updated_at, created_at))
                                 < datetime('now', ?)
                            THEN 1 ELSE 0 END"""
        params.append(f"-{stale_seconds} seconds")
    else:
        stale_expr = "0"
    if org_id is not None:
        params.append(org_id)
    row = db.one(f"""SELECT
                        COUNT(*) AS total,
                        COALESCE(SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END), 0)
                            AS queued,
                        COALESCE(SUM(CASE WHEN status='running' THEN 1 ELSE 0 END), 0)
                            AS running,
                        COALESCE(SUM({stale_expr}), 0) AS stale_running,
                        COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0)
                            AS failed,
                        COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END), 0)
                            AS done,
                        COALESCE(SUM(CASE
                            WHEN status='failed'
                             AND datetime(COALESCE(updated_at, created_at))
                                 >= datetime('now', '-24 hours')
                            THEN 1 ELSE 0 END), 0) AS failed_24h,
                        COALESCE(SUM(CASE
                            WHEN status='done'
                             AND datetime(COALESCE(completed_at, updated_at, created_at))
                                 >= datetime('now', '-24 hours')
                            THEN 1 ELSE 0 END), 0) AS done_24h,
                        MAX(completed_at) AS last_completed_at
                     FROM jobs
                     {where_sql}""", tuple(params))
    if org_id is None:
        active_where = "WHERE status IN ('queued','running')"
        active_params: tuple = ()
    else:
        active_where = "WHERE org_id=? AND status IN ('queued','running')"
        active_params = (org_id,)
    oldest = db.one(f"""SELECT id, status, created_at,
                               CAST((julianday('now') - julianday(created_at)) * 86400
                                    AS INTEGER) AS age_seconds
                        FROM jobs
                        {active_where}
                        ORDER BY created_at, id
                        LIMIT 1""", active_params)
    done_24h = int(row["done_24h"] if row else 0)
    failed_24h = int(row["failed_24h"] if row else 0)
    recent_attempts = done_24h + failed_24h
    failure_rate = round((failed_24h / recent_attempts) * 100) if recent_attempts else 0
    failed = int(row["failed"] if row else 0)
    queued = int(row["queued"] if row else 0)
    running = int(row["running"] if row else 0)
    stale_running = int(row["stale_running"] if row else 0)
    if stale_running:
        health_label = "stale"
    elif failed:
        health_label = "needs retry"
    elif queued or running:
        health_label = "working"
    else:
        health_label = "clear"
    age_seconds = oldest["age_seconds"] if oldest else None
    return {
        "total": int(row["total"] if row else 0),
        "queued": queued,
        "running": running,
        "stale_running": stale_running,
        "failed": failed,
        "done": int(row["done"] if row else 0),
        "done_24h": done_24h,
        "failed_24h": failed_24h,
        "failure_rate_24h": failure_rate,
        "oldest_active_job_id": oldest["id"] if oldest else None,
        "oldest_active_status": oldest["status"] if oldest else None,
        "oldest_active_at": oldest["created_at"] if oldest else None,
        "oldest_active_age_seconds": age_seconds,
        "oldest_active_label": _duration_label(age_seconds),
        "last_completed_at": row["last_completed_at"] if row else None,
        "health_label": health_label,
    }


def queue_stats_for_org(org_id: int) -> dict:
    return queue_stats(org_id)


def pending_count(org_id: int | None = None) -> int:
    if org_id is None:
        row = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")
    else:
        row = db.one("""SELECT COUNT(*) AS n FROM jobs
                        WHERE org_id=? AND status IN ('queued','running')""", (org_id,))
    return row["n"] if row else 0


def pending_pack_count(org_id: int) -> int:
    row = db.one("""SELECT COUNT(*) AS n FROM jobs
                    WHERE org_id=? AND kind IN ('generate_pack','regenerate_pack')
                      AND status IN ('queued','running')""", (org_id,))
    return row["n"] if row else 0


def failed_count(org_id: int | None = None) -> int:
    if org_id is None:
        row = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='failed'")
    else:
        row = db.one("SELECT COUNT(*) AS n FROM jobs WHERE org_id=? AND status='failed'",
                     (org_id,))
    return row["n"] if row else 0
