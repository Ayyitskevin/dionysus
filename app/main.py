"""Dionysus / Platekit — Photography AI SaaS for restaurants and photographers."""

import datetime as dt
import json
import logging

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import billing, config, db, jobs, plans, readiness, recipes, security
from .render import ROOT, templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Dionysus", version="0.2.0", docs_url=None,
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
    if exc.status_code in (402, 403, 404, 410) and             "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            request, "error.html", {"status": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code)
    return await http_exception_handler(request, exc)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "dionysus", "jobs_pending": jobs.pending_count()}


@app.get("/readiness")
async def readiness_check():
    return readiness.summary()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "recipes": recipes.active()[:4],
        "plans": plans.all_plans(),
    })


@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    return templates.TemplateResponse(request, "pricing.html", {"plans": plans.all_plans()})


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
    if not user or not security.verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid email or password."},
            status_code=401)
    db.run("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (user["id"],))
    member = db.one("""SELECT o.slug FROM organization_members om
                       JOIN organizations o ON o.id=om.org_id
                       WHERE om.user_id=? ORDER BY om.created_at LIMIT 1""",
                    (user["id"],))
    target = f"/w/{member['slug']}" if member else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(security.USER_COOKIE, security.user_cookie(user["id"]),
                    max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", path="/")
    if member:
        resp.set_cookie(security.WORKSPACE_COOKIE, security.workspace_cookie(member["slug"]),
                        max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(security.USER_COOKIE, path="/")
    resp.delete_cookie(security.WORKSPACE_COOKIE, path="/")
    return resp


def _current_user(request: Request):
    user_id = security.user_id_from_request(request)
    if not user_id:
        return None
    return db.one("SELECT * FROM users WHERE id=?", (user_id,))


def _org_by_slug(slug: str):
    org = db.one("SELECT * FROM organizations WHERE slug=?", (slug,))
    if not org:
        raise HTTPException(status_code=404, detail="workspace not found")
    return org


def _membership(user_id: int, org_id: int):
    return db.one("""SELECT * FROM organization_members
                     WHERE user_id=? AND org_id=?""", (user_id, org_id))


def _require_workspace(request: Request, slug: str):
    org = _org_by_slug(slug)
    user = _current_user(request)
    if user and _membership(user["id"], org["id"]):
        return org, user
    if security.has_workspace_access(request, slug):
        return org, user
    raise HTTPException(status_code=403, detail="workspace access required")


def _set_auth_cookies(resp: RedirectResponse, user_id: int, slug: str) -> RedirectResponse:
    resp.set_cookie(security.USER_COOKIE, security.user_cookie(user_id),
                    max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", path="/")
    resp.set_cookie(security.WORKSPACE_COOKIE, security.workspace_cookie(slug),
                    max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/signup")
async def signup(name: str = Form(...), email: str = Form(...),
                 password: str = Form(...), audience: str = Form(...),
                 company: str = Form(""), plan: str = Form("restaurant_starter")):
    if audience not in ("restaurant", "photographer"):
        raise HTTPException(status_code=400, detail="choose restaurant or photographer")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    email = email.strip().lower()
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        raise HTTPException(status_code=400, detail="account already exists; log in")
    plan = plans.normalize_plan(plan, audience)
    base = security.slugify(company or name)
    slug = base
    n = 2
    while db.one("SELECT id FROM organizations WHERE slug=?", (slug,)):
        slug = f"{base}-{n}"
        n += 1
    with db.tx() as con:
        cur = con.execute("""INSERT INTO users (email, name, password_hash)
                             VALUES (?,?,?)""",
                          (email, name.strip(), security.hash_password(password)))
        user_id = cur.lastrowid
        cur = con.execute("""INSERT INTO organizations
                             (slug, name, email, audience, company, plan, access_token)
                             VALUES (?,?,?,?,?,?,?)""",
                          (slug, name.strip(), email, audience,
                           company.strip() or None, plan, security.new_token()))
        org_id = cur.lastrowid
        con.execute("""INSERT INTO organization_members (org_id, user_id, role)
                       VALUES (?,?, 'owner')""", (org_id, user_id))
        seed_title = "First monthly content pack" if audience == "restaurant" else             "First client campaign kit"
        con.execute("INSERT INTO campaigns (org_id, title, goal) VALUES (?,?,?)",
                    (org_id, seed_title, "Create sellable food-photo content from one shoot"))
    billing.sync_trial_subscription(org_id, plan)
    resp = RedirectResponse(f"/w/{slug}", status_code=303)
    return _set_auth_cookies(resp, user_id, slug)


@app.get("/w/{slug}", response_class=HTMLResponse)
async def workspace(request: Request, slug: str):
    org, user = _require_workspace(request, slug)
    menu = db.all_("SELECT * FROM menu_items WHERE org_id=? ORDER BY id DESC", (org["id"],))
    campaigns = db.all_("SELECT * FROM campaigns WHERE org_id=? ORDER BY id DESC", (org["id"],))
    packs = db.all_("""SELECT cp.*, cr.name AS recipe_name
                       FROM content_packs cp JOIN content_recipes cr ON cr.id=cp.recipe_id
                       WHERE cp.org_id=? ORDER BY cp.created_at DESC""", (org["id"],))
    sub = billing.checkout_state(org)
    limit = plans.pack_limit(sub["plan"])
    period = dt.date.today().strftime("%Y-%m")
    used = db.one("""SELECT COUNT(*) AS n FROM content_packs
                     WHERE org_id=? AND substr(created_at,1,7)=?""",
                  (org["id"], period))["n"]
    return templates.TemplateResponse(request, "workspace.html", {
        "org": org, "user": user, "menu": menu, "campaigns": campaigns,
        "packs": packs, "recipes": recipes.active(), "subscription": sub,
        "pack_limit": limit, "pack_used": used,
        "pack_json": {p["id"]: json.loads(p["body_json"]) for p in packs},
    })


@app.post("/w/{slug}/profile")
async def update_profile(request: Request, slug: str, brand_voice: str = Form(""),
                         market: str = Form(""), service_mix: str = Form("")):
    org, _ = _require_workspace(request, slug)
    db.run("""UPDATE organizations SET brand_voice=?, market=?, service_mix=?
              WHERE id=?""",
           (brand_voice.strip() or None, market.strip() or None,
            service_mix.strip() or None, org["id"]))
    return RedirectResponse(f"/w/{slug}", status_code=303)


@app.post("/w/{slug}/menu")
async def add_menu_item(request: Request, slug: str, name: str = Form(...),
                        category: str = Form(""), notes: str = Form("")):
    org, _ = _require_workspace(request, slug)
    if not name.strip():
        raise HTTPException(status_code=400, detail="menu item name required")
    db.run("INSERT INTO menu_items (org_id, name, category, notes) VALUES (?,?,?,?)",
           (org["id"], name.strip(), category.strip() or None, notes.strip() or None))
    return RedirectResponse(f"/w/{slug}", status_code=303)


@app.post("/w/{slug}/campaigns")
async def add_campaign(request: Request, slug: str, title: str = Form(...),
                       goal: str = Form(""), launch_date: str = Form("")):
    org, _ = _require_workspace(request, slug)
    db.run("""INSERT INTO campaigns (org_id, title, goal, launch_date)
              VALUES (?,?,?,?)""",
           (org["id"], title.strip(), goal.strip() or None, launch_date.strip() or None))
    return RedirectResponse(f"/w/{slug}", status_code=303)


@app.post("/w/{slug}/campaigns/{campaign_id}/generate")
async def generate_pack(request: Request, slug: str, campaign_id: int,
                        recipe_id: int = Form(...)):
    org, _ = _require_workspace(request, slug)
    campaign = db.one("SELECT * FROM campaigns WHERE id=? AND org_id=?",
                      (campaign_id, org["id"]))
    if not campaign:
        raise HTTPException(status_code=404, detail="campaign not found")
    recipe = recipes.by_id(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="recipe not found")
    sub = billing.checkout_state(org)
    if not plans.allowed_recipe(sub["plan"], recipe["slug"]):
        raise HTTPException(status_code=402, detail="upgrade required for this recipe")
    limit = plans.pack_limit(sub["plan"])
    if limit is not None:
        period = dt.date.today().strftime("%Y-%m")
        used = db.one("""SELECT COUNT(*) AS n FROM content_packs
                         WHERE org_id=? AND substr(created_at,1,7)=?""",
                      (org["id"], period))["n"]
        if used >= limit:
            raise HTTPException(status_code=402, detail="monthly pack limit reached")
    jobs.enqueue_generate(campaign_id, recipe_id)
    return RedirectResponse(f"/w/{slug}#packs", status_code=303)


@app.get("/w/{slug}/billing", response_class=HTMLResponse)
async def billing_page(request: Request, slug: str):
    org, _ = _require_workspace(request, slug)
    return templates.TemplateResponse(request, "billing.html", {
        "org": org,
        "subscription": billing.checkout_state(org),
        "plans": plans.all_plans(),
    })


@app.post("/w/{slug}/billing/checkout")
async def start_checkout(request: Request, slug: str):
    org, _ = _require_workspace(request, slug)
    url = billing.create_checkout_session(
        org,
        success_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=success",
        cancel_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=cancel",
    )
    return RedirectResponse(url, status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    event = await billing.construct_webhook_event(request)
    return billing.handle_event(event)


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
