"""Dionysus / Platekit — Photography AI SaaS for restaurants and photographers."""

import json
import logging

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, jobs, recipes, security
from .render import ROOT, templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Dionysus", version="0.1.0", docs_url=None,
              redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.on_event("startup")
async def startup():
    db.migrate()


@app.middleware("http")
async def common_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path not in {"/", "/pricing", "/healthz"}:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.exception_handler(StarletteHTTPException)
async def branded_errors(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (403, 404, 410) and "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            request, "error.html", {"status": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code)
    return await http_exception_handler(request, exc)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "dionysus", "jobs_pending": jobs.pending_count()}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "recipes": recipes.active()[:4],
        "plans": _plans(),
    })


@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    return templates.TemplateResponse(request, "pricing.html", {"plans": _plans()})


def _plans() -> list[dict]:
    return [
        {"name": "Restaurant Starter", "price": 4900,
         "for": "Owners who need weekly posts from existing shoots",
         "features": ["12 captions/month", "3 campaign briefs", "delivery-app copy"]},
        {"name": "Restaurant Growth", "price": 14900,
         "for": "Operators with seasonal menus and paid campaigns",
         "features": ["Unlimited draft packs", "menu-launch calendar", "license prompts"]},
        {"name": "Photographer Studio", "price": 9900,
         "for": "Food photographers selling retainers and add-ons",
         "features": ["Client intake workspaces", "shot-list packs", "upsell scripts"]},
    ]


def _org_by_slug(slug: str):
    org = db.one("SELECT * FROM organizations WHERE slug=?", (slug,))
    if not org:
        raise HTTPException(status_code=404, detail="workspace not found")
    return org


def _require_workspace(request: Request, slug: str):
    org = _org_by_slug(slug)
    if not security.has_workspace_access(request, slug):
        raise HTTPException(status_code=403, detail="workspace access required")
    return org


@app.post("/signup")
async def signup(name: str = Form(...), email: str = Form(...),
                 audience: str = Form(...), company: str = Form(""),
                 plan: str = Form("starter")):
    if audience not in ("restaurant", "photographer"):
        raise HTTPException(status_code=400, detail="choose restaurant or photographer")
    base = security.slugify(company or name)
    slug = base
    n = 2
    while db.one("SELECT id FROM organizations WHERE slug=?", (slug,)):
        slug = f"{base}-{n}"
        n += 1
    org_id = db.run("""INSERT INTO organizations
                       (slug, name, email, audience, company, plan, access_token)
                       VALUES (?,?,?,?,?,?,?)""",
                    (slug, name.strip(), email.strip().lower(), audience,
                     company.strip() or None, plan, security.new_token()))
    seed_title = "First monthly content pack" if audience == "restaurant" else \
        "First client campaign kit"
    db.run("INSERT INTO campaigns (org_id, title, goal) VALUES (?,?,?)",
           (org_id, seed_title, "Create sellable food-photo content from one shoot"))
    resp = RedirectResponse(f"/w/{slug}", status_code=303)
    resp.set_cookie(security.WORKSPACE_COOKIE, security.workspace_cookie(slug),
                    max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", path="/")
    return resp


@app.get("/w/{slug}", response_class=HTMLResponse)
async def workspace(request: Request, slug: str):
    org = _require_workspace(request, slug)
    menu = db.all_("SELECT * FROM menu_items WHERE org_id=? ORDER BY id DESC", (org["id"],))
    campaigns = db.all_("SELECT * FROM campaigns WHERE org_id=? ORDER BY id DESC", (org["id"],))
    packs = db.all_("""SELECT cp.*, cr.name AS recipe_name
                       FROM content_packs cp JOIN content_recipes cr ON cr.id=cp.recipe_id
                       WHERE cp.org_id=? ORDER BY cp.created_at DESC""", (org["id"],))
    return templates.TemplateResponse(request, "workspace.html", {
        "org": org, "menu": menu, "campaigns": campaigns,
        "packs": packs, "recipes": recipes.active(),
        "pack_json": {p["id"]: json.loads(p["body_json"]) for p in packs},
    })


@app.post("/w/{slug}/profile")
async def update_profile(request: Request, slug: str, brand_voice: str = Form(""),
                         market: str = Form(""), service_mix: str = Form("")):
    org = _require_workspace(request, slug)
    db.run("""UPDATE organizations SET brand_voice=?, market=?, service_mix=?
              WHERE id=?""",
           (brand_voice.strip() or None, market.strip() or None,
            service_mix.strip() or None, org["id"]))
    return RedirectResponse(f"/w/{slug}", status_code=303)


@app.post("/w/{slug}/menu")
async def add_menu_item(request: Request, slug: str, name: str = Form(...),
                        category: str = Form(""), notes: str = Form("")):
    org = _require_workspace(request, slug)
    if not name.strip():
        raise HTTPException(status_code=400, detail="menu item name required")
    db.run("INSERT INTO menu_items (org_id, name, category, notes) VALUES (?,?,?,?)",
           (org["id"], name.strip(), category.strip() or None, notes.strip() or None))
    return RedirectResponse(f"/w/{slug}", status_code=303)


@app.post("/w/{slug}/campaigns")
async def add_campaign(request: Request, slug: str, title: str = Form(...),
                       goal: str = Form(""), launch_date: str = Form("")):
    org = _require_workspace(request, slug)
    db.run("""INSERT INTO campaigns (org_id, title, goal, launch_date)
              VALUES (?,?,?,?)""",
           (org["id"], title.strip(), goal.strip() or None, launch_date.strip() or None))
    return RedirectResponse(f"/w/{slug}", status_code=303)


@app.post("/w/{slug}/campaigns/{campaign_id}/generate")
async def generate_pack(request: Request, slug: str, campaign_id: int,
                        recipe_id: int = Form(...)):
    org = _require_workspace(request, slug)
    campaign = db.one("SELECT * FROM campaigns WHERE id=? AND org_id=?",
                      (campaign_id, org["id"]))
    if not campaign:
        raise HTTPException(status_code=404, detail="campaign not found")
    jobs.enqueue_generate(campaign_id, recipe_id)
    return RedirectResponse(f"/w/{slug}#packs", status_code=303)


@app.get("/api/mise/organizations/{slug}/latest-pack",
         dependencies=[Depends(security.require_mise_token)])
async def latest_pack_for_mise(slug: str):
    org = _org_by_slug(slug)
    pack = db.one("""SELECT cp.*, cr.slug AS recipe_slug FROM content_packs cp
                     JOIN content_recipes cr ON cr.id=cp.recipe_id
                     WHERE cp.org_id=? ORDER BY cp.created_at DESC LIMIT 1""",
                  (org["id"],))
    if not pack:
        return {"matched": True, "org": slug, "pack": None}
    return {"matched": True, "org": slug, "pack": json.loads(pack["body_json"])}
