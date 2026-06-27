"""Dionysus — stateless Mise content worker.

Bearer-gated draft APIs only (print pitch + campaign packs). Identity, billing,
and the human review/accept workflow live in Mise. The legacy SaaS UI/auth was
removed; see RETIRE.md.
"""

from contextlib import asynccontextmanager
import logging
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import (
    config, contract, db, jobs, mise_hook, model_client,
    packs as pack_utils, readiness, security,
)
from .render import ROOT, templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    yield


app = FastAPI(title="Dionysus", version="0.3.0", docs_url=None,
              redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def common_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path not in {"/", "/healthz"}:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "service": "dionysus",
        "jobs_pending": jobs.pending_count(),
        "jobs_failed": jobs.failed_count(),
        "queue": jobs.queue_stats(),
        "studio_mode": config.STUDIO_MODE,
        "model": {
            "enabled": model_client.is_enabled(),
            "name": config.MODEL_NAME or None,
            "endpoint_configured": bool(config.MODEL_ENDPOINT),
        },
        "studio": {
            "mise_bridge_armed": bool(config.MISE_IMPORT_TOKEN),
            "demo_org": "blue-plate",
            "print_pitch_path": "/api/mise/organizations/{slug}/print-pitch",
            "argus_pack_path": "/api/mise/organizations/{slug}/argus-pack",
        },
    }


@app.get("/readiness")
async def readiness_check():
    return readiness.summary()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "studio_status.html",
        {"demo_org": "blue-plate", "mise_bridge": bool(config.MISE_IMPORT_TOKEN)},
    )


def _org_by_slug(slug: str):
    org = db.one("SELECT * FROM organizations WHERE slug=?", (slug,))
    if not org:
        raise HTTPException(status_code=404, detail="organization not found")
    return org


def _correlation_id(body: dict) -> str | None:
    """Optional Mise correlation id, echoed back so Mise can tie a draft to the
    originating request."""
    value = body.get("correlation_id")
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="correlation_id must be a string")
    return value.strip() or None


def _job_status_payload(job: dict) -> dict:
    return {
        "id": job["id"],
        "kind": job["kind"],
        "summary": job["summary"],
        "status": job["status"],
        "attempts": job["attempts"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "completed_at": job["completed_at"],
        "source_pack_id": job["source_pack_id"],
        "source_pack_title": job["source_pack_title"],
        "result_pack_id": job["result_pack_id"],
        "result_pack_title": job["result_pack_title"],
        "terminal": job["status"] in ("done", "failed"),
    }


def _pack_api_payload(pack) -> dict:
    body = pack_utils.body(pack)
    return {
        "id": pack["id"],
        "title": pack["title"],
        "status": pack["status"],
        "recipe": {"id": pack["recipe_id"], "slug": pack["recipe_slug"],
                   "name": pack["recipe_name"]},
        "campaign": {"id": pack["campaign_id"], "title": pack["campaign_title"]},
        "created_at": pack["created_at"],
        "approved_at": pack["approved_at"],
        "exported_at": pack["exported_at"],
        "source_pack_id": pack["source_pack_id"],
        "revision_note": pack["revision_note"],
        "archived_at": pack["archived_at"],
        "human_edited": bool(pack["human_edited"]),
        "markdown": pack_utils.markdown(pack),
        "body": body,
        "contract": contract.envelope_for_pack(
            body, title=pack["title"], ai_model=pack["ai_model"]),
    }


@app.get("/api/mise/organizations/{slug}/jobs/{job_id}",
         dependencies=[Depends(security.require_mise_token)])
async def job_status_for_mise(slug: str, job_id: int):
    org = _org_by_slug(slug)
    job = jobs.get_for_org(job_id, org["id"])
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, "org": slug, "job": _job_status_payload(job)}


@app.get("/api/mise/organizations/{slug}/packs",
         dependencies=[Depends(security.require_mise_token)])
async def packs_for_mise(slug: str, include_drafts: bool = False):
    org = _org_by_slug(slug)
    where = "cp.org_id=? AND cp.archived_at IS NULL"
    params: list = [org["id"]]
    if not include_drafts:
        where += " AND cp.status IN ('approved','exported')"
    rows = db.all_(f"""SELECT cp.*, cr.slug AS recipe_slug, cr.name AS recipe_name,
                              c.title AS campaign_title,
                              o.name AS org_name, o.company, o.audience
                       FROM content_packs cp
                       JOIN content_recipes cr ON cr.id=cp.recipe_id
                       JOIN campaigns c ON c.id=cp.campaign_id
                       JOIN organizations o ON o.id=cp.org_id
                       WHERE {where}
                       ORDER BY cp.created_at DESC, cp.id DESC""", tuple(params))
    return {"matched": True, "org": slug,
            "packs": [_pack_api_payload(p) for p in rows]}


@app.get("/api/mise/organizations/{slug}/latest-pack",
         dependencies=[Depends(security.require_mise_token)])
async def latest_pack_for_mise(slug: str, include_drafts: bool = False):
    org = _org_by_slug(slug)
    where = "cp.org_id=? AND cp.archived_at IS NULL"
    params: list = [org["id"]]
    if not include_drafts:
        where += " AND cp.status IN ('approved','exported')"
    pack = db.one(f"""SELECT cp.*, cr.slug AS recipe_slug, cr.name AS recipe_name,
                             c.title AS campaign_title,
                             o.name AS org_name, o.company, o.audience
                      FROM content_packs cp
                      JOIN content_recipes cr ON cr.id=cp.recipe_id
                      JOIN campaigns c ON c.id=cp.campaign_id
                      JOIN organizations o ON o.id=cp.org_id
                      WHERE {where}
                      ORDER BY cp.created_at DESC, cp.id DESC LIMIT 1""",
                  tuple(params))
    if not pack:
        return {"matched": True, "org": slug, "pack": None}
    return {"matched": True, "org": slug, "pack": _pack_api_payload(pack)}


@app.post("/api/mise/organizations/{slug}/packs/{pack_id}/human-edited",
          dependencies=[Depends(security.require_mise_token)])
async def mark_pack_human_edited(slug: str, pack_id: int):
    """Mise marks a draft human-edited so the worker never overwrites it."""
    org = _org_by_slug(slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    db.run("""UPDATE content_packs SET human_edited=1, updated_at=datetime('now')
              WHERE id=?""", (pack_id,))
    return {"ok": True, "org": slug, "pack_id": pack_id, "human_edited": True}


@app.post("/api/mise/organizations/{slug}/print-pitch",
          dependencies=[Depends(security.require_mise_token)])
async def mise_print_pitch(slug: str, request: Request):
    """Plutus hand-off — enrich client print upsell email copy from bundle rationale."""
    _org_by_slug(slug)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json body required")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="json object required")
    gallery_name = body.get("gallery_name")
    if not isinstance(gallery_name, str) or not gallery_name.strip():
        raise HTTPException(status_code=400, detail="gallery_name required")
    bundles = body.get("bundles")
    if not isinstance(bundles, list):
        raise HTTPException(status_code=400, detail="bundles must be a list")
    try:
        photo_count = int(body.get("photo_count") or 0)
        estimated_total_cents = int(body.get("estimated_total_cents") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="photo_count and estimated_total_cents required")
    argus_run_id = body.get("argus_run_id")
    if argus_run_id is not None:
        try:
            argus_run_id = int(argus_run_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="argus_run_id must be an integer")
    gallery_theme = body.get("gallery_theme")
    if gallery_theme is not None and not isinstance(gallery_theme, str):
        raise HTTPException(status_code=400, detail="gallery_theme must be a string")
    from . import print_pitch

    start = time.perf_counter()
    pitch = print_pitch.build_print_pitch(
        gallery_name=gallery_name.strip(),
        bundles=bundles,
        photo_count=photo_count,
        estimated_total_cents=estimated_total_cents,
        gallery_theme=(gallery_theme or "").strip() or None,
        argus_run_id=argus_run_id,
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    response = {
        "ok": True,
        "org": slug,
        **pitch,
        "contract": contract.envelope(
            contract.drafts_from_print_pitch(pitch),
            latency_ms=latency_ms,
        ),
    }
    correlation_id = _correlation_id(body)
    if correlation_id:
        response["correlation_id"] = correlation_id
    return response


@app.post("/api/mise/organizations/{slug}/argus-pack",
          dependencies=[Depends(security.require_mise_token)])
async def mise_argus_pack_hook(slug: str, request: Request):
    """Mise Argus callback hook — draft a keyword-enriched pack from one run."""
    org = _org_by_slug(slug)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json body required")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="json object required")
    try:
        argus_run_id = int(body.get("argus_run_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="argus_run_id must be an integer")
    if argus_run_id <= 0:
        raise HTTPException(status_code=400, detail="argus_run_id required")
    mise_gallery_id = body.get("mise_gallery_id")
    if mise_gallery_id is not None:
        try:
            mise_gallery_id = int(mise_gallery_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="mise_gallery_id must be an integer")
        if mise_gallery_id <= 0:
            mise_gallery_id = None
    gallery_title = body.get("gallery_title")
    if gallery_title is not None and not isinstance(gallery_title, str):
        raise HTTPException(status_code=400, detail="gallery_title must be a string")
    recipe_slug = body.get("recipe_slug")
    if recipe_slug is not None and not isinstance(recipe_slug, str):
        raise HTTPException(status_code=400, detail="recipe_slug must be a string")
    correlation_id = _correlation_id(body)
    try:
        result = mise_hook.generate_from_argus(
            org,
            argus_run_id=argus_run_id,
            mise_gallery_id=mise_gallery_id,
            gallery_title=gallery_title,
            recipe_slug=(recipe_slug or "").strip() or None,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "org": slug, **result}
