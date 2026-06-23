"""Dionysus / Platekit — Photography AI SaaS for restaurants and photographers."""

import datetime as dt
import json
import logging
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import billing, config, db, jobs, packs as pack_utils, plans, readiness, recipes, security
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
    if exc.status_code == 403 and _should_redirect_to_login(request):
        return RedirectResponse(_login_url_for_request(request), status_code=303)
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


def _signup_state(data, *, normalize: bool = True) -> dict:
    audience = (data.get("audience") or "restaurant").strip().lower()
    if audience not in ("restaurant", "photographer"):
        audience = "restaurant"
    raw_plan = data.get("plan") or ""
    plan = plans.normalize_plan(raw_plan, audience) if normalize else raw_plan
    return {
        "name": data.get("name", ""),
        "email": data.get("email", ""),
        "company": data.get("company", ""),
        "audience": audience,
        "plan": plan,
        "market": data.get("market", ""),
        "service_mix": data.get("service_mix", ""),
        "brand_voice": data.get("brand_voice", ""),
        "first_item": data.get("first_item", ""),
        "first_item_note": data.get("first_item_note", ""),
        "campaign_goal": data.get("campaign_goal", ""),
        "launch_date": data.get("launch_date", ""),
    }


def _signup_defaults(request: Request) -> dict:
    return _signup_state(request.query_params)


def _home_context(request: Request, *, signup: dict | None = None,
                  signup_error: str | None = None) -> dict:
    return {
        "recipes": recipes.active()[:4],
        "plans": plans.all_plans(),
        "signup": signup or _signup_defaults(request),
        "signup_error": signup_error,
    }


def _safe_next(raw_next: str | None) -> str:
    if not raw_next:
        return ""
    parsed = urlparse(raw_next)
    if parsed.scheme or parsed.netloc:
        return ""
    if not raw_next.startswith("/") or raw_next.startswith("//") or "\\" in raw_next:
        return ""
    if not (raw_next == "/" or raw_next.startswith("/w/")):
        return ""
    return raw_next


def _workspace_page_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "w":
        return True
    return len(parts) == 3 and parts[0] == "w" and parts[2] in {
        "billing", "settings"}


def _should_redirect_to_login(request: Request) -> bool:
    return (
        request.method == "GET"
        and _workspace_page_path(request.url.path)
        and not security.user_id_from_request(request)
    )


def _login_url_for_request(request: Request) -> str:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return f"/login?next={quote(target, safe='')}"


def _workspace_slug_from_next(target: str) -> str:
    parts = [part for part in urlparse(target).path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "w":
        return parts[1]
    return ""


def _login_destination(user_id: int, raw_next: str) -> tuple[str, str | None]:
    safe_next = _safe_next(raw_next)
    if safe_next:
        requested_slug = _workspace_slug_from_next(safe_next)
        if requested_slug:
            member = db.one("""SELECT o.slug FROM organization_members om
                               JOIN organizations o ON o.id=om.org_id
                               WHERE om.user_id=? AND o.slug=?""",
                            (user_id, requested_slug))
            if member:
                return safe_next, member["slug"]
    member = db.one("""SELECT o.slug FROM organization_members om
                       JOIN organizations o ON o.id=om.org_id
                       WHERE om.user_id=? ORDER BY om.created_at LIMIT 1""",
                    (user_id,))
    if member:
        return f"/w/{member['slug']}", member["slug"]
    return "/", None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", _home_context(request))


@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    return templates.TemplateResponse(request, "pricing.html", {"plans": plans.all_plans()})


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {
        "error": None,
        "email": request.query_params.get("email", ""),
        "next": _safe_next(request.query_params.get("next", "")),
    })


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...),
                next: str = Form("")):
    email = email.strip().lower()
    user = db.one("SELECT * FROM users WHERE email=?", (email,))
    safe_next = _safe_next(next)
    if not user or not security.verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html", {
                "error": "Invalid email or password.",
                "email": email,
                "next": safe_next,
            },
            status_code=401)
    db.run("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (user["id"],))
    target, workspace_slug = _login_destination(user["id"], safe_next)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(security.USER_COOKIE, security.user_cookie(user["id"]),
                    **_auth_cookie_kwargs())
    if workspace_slug:
        resp.set_cookie(security.WORKSPACE_COOKIE, security.workspace_cookie(workspace_slug),
                        **_auth_cookie_kwargs())
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


def _require_owner(request: Request, slug: str):
    org = _org_by_slug(slug)
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="login required")
    member = _membership(user["id"], org["id"])
    if not member or member["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="owner access required")
    return org, user, member


def _auth_cookie_kwargs() -> dict:
    return {
        "max_age": 60 * 60 * 24 * 90,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "secure": config.COOKIE_SECURE,
    }


def _set_auth_cookies(resp: RedirectResponse, user_id: int, slug: str) -> RedirectResponse:
    resp.set_cookie(security.USER_COOKIE, security.user_cookie(user_id),
                    **_auth_cookie_kwargs())
    resp.set_cookie(security.WORKSPACE_COOKIE, security.workspace_cookie(slug),
                    **_auth_cookie_kwargs())
    return resp


def _first_recipe_slug(audience: str, plan: str) -> str:
    if audience == "photographer":
        return "photographer-upsell"
    if plan == "restaurant_starter":
        return "menu-launch"
    return "monthly-retainer"


@app.post("/signup")
async def signup(request: Request, name: str = Form(...), email: str = Form(...),
                 password: str = Form(...), audience: str = Form(...),
                 company: str = Form(""), plan: str = Form("restaurant_starter"),
                 market: str = Form(""), brand_voice: str = Form(""),
                 service_mix: str = Form(""), first_item: str = Form(""),
                 first_item_note: str = Form(""), campaign_goal: str = Form(""),
                 launch_date: str = Form("")):
    if audience not in ("restaurant", "photographer"):
        raise HTTPException(status_code=400, detail="choose restaurant or photographer")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    email = email.strip().lower()
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        signup_state = _signup_state({
            "name": name.strip(),
            "email": email,
            "company": company.strip(),
            "audience": audience,
            "plan": plan,
            "market": market,
            "service_mix": service_mix,
            "brand_voice": brand_voice,
            "first_item": first_item,
            "first_item_note": first_item_note,
            "campaign_goal": campaign_goal,
            "launch_date": launch_date,
        })
        return templates.TemplateResponse(
            request, "home.html",
            _home_context(
                request,
                signup=signup_state,
                signup_error="An account already exists for this email. Log in instead.",
            ),
            status_code=400)
    plan = plans.normalize_plan(plan, audience)
    base = security.slugify(company or name)
    slug = base
    n = 2
    while db.one("SELECT id FROM organizations WHERE slug=?", (slug,)):
        slug = f"{base}-{n}"
        n += 1
    market = market.strip()
    brand_voice = brand_voice.strip()
    service_mix = service_mix.strip()
    first_item = first_item.strip() or (
        "seasonal hero dish" if audience == "restaurant" else "ideal restaurant client")
    first_item_note = first_item_note.strip()
    campaign_goal = campaign_goal.strip() or (
        "turn one shoot into a month of restaurant marketing"
        if audience == "restaurant" else
        "sell a recurring content package to a food client")
    seed_title = "First monthly content pack" if audience == "restaurant" else \
        "First client campaign kit"
    recipe_slug = _first_recipe_slug(audience, plan)
    with db.tx() as con:
        cur = con.execute("""INSERT INTO users (email, name, password_hash)
                             VALUES (?,?,?)""",
                          (email, name.strip(), security.hash_password(password)))
        user_id = cur.lastrowid
        cur = con.execute("""INSERT INTO organizations
                             (slug, name, email, audience, company, plan, access_token,
                              market, service_mix, brand_voice)
                             VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (slug, name.strip(), email, audience,
                           company.strip() or None, plan, security.new_token(),
                           market or None, service_mix or None, brand_voice or None))
        org_id = cur.lastrowid
        con.execute("""INSERT INTO organization_members (org_id, user_id, role)
                       VALUES (?,?, 'owner')""", (org_id, user_id))
        con.execute("""INSERT INTO menu_items (org_id, name, category, notes)
                       VALUES (?,?,?,?)""",
                    (org_id, first_item,
                     "priority dish" if audience == "restaurant" else "client target",
                     first_item_note or None))
        cur = con.execute("""INSERT INTO campaigns (org_id, title, goal, launch_date)
                             VALUES (?,?,?,?)""",
                          (org_id, seed_title, campaign_goal, launch_date.strip() or None))
        campaign_id = cur.lastrowid
    billing.sync_trial_subscription(org_id, plan)
    recipe = db.one("SELECT id FROM content_recipes WHERE slug=?", (recipe_slug,))
    if recipe:
        jobs.enqueue_generate(campaign_id, recipe["id"])
    resp = RedirectResponse(f"/w/{slug}#packs", status_code=303)
    return _set_auth_cookies(resp, user_id, slug)


@app.get("/w/{slug}", response_class=HTMLResponse)
async def workspace(request: Request, slug: str):
    org, user = _require_workspace(request, slug)
    menu = db.all_("SELECT * FROM menu_items WHERE org_id=? ORDER BY id DESC", (org["id"],))
    campaigns = db.all_("SELECT * FROM campaigns WHERE org_id=? ORDER BY id DESC", (org["id"],))
    packs = db.all_("""SELECT cp.*, cr.name AS recipe_name
                       FROM content_packs cp JOIN content_recipes cr ON cr.id=cp.recipe_id
                       WHERE cp.org_id=? ORDER BY cp.created_at DESC""", (org["id"],))
    latest_pack = packs[0] if packs else None
    shared_pack_raw = request.query_params.get("shared", "")
    try:
        shared_pack_id = int(shared_pack_raw)
    except ValueError:
        shared_pack_id = 0
    sub = billing.checkout_state(org)
    limit = plans.pack_limit(sub["plan"])
    period = dt.date.today().strftime("%Y-%m")
    used = db.one("""SELECT COUNT(*) AS n FROM content_packs
                     WHERE org_id=? AND substr(created_at,1,7)=?""",
                  (org["id"], period))["n"]
    active_recipes = recipes.active()
    upgrade_recipes = {
        r["id"]: plans.upgrade_plan_for_recipe(org["audience"], sub["plan"], r["slug"])
        for r in active_recipes
    }
    limit_upgrade_plan = plans.upgrade_plan_for_limit(org["audience"], sub["plan"])
    return templates.TemplateResponse(request, "workspace.html", {
        "org": org, "user": user, "menu": menu, "campaigns": campaigns,
        "packs": packs, "latest_pack": latest_pack, "recipes": active_recipes,
        "subscription": sub, "pack_limit": limit, "pack_used": used,
        "limit_upgrade_plan": limit_upgrade_plan,
        "upgrade_recipes": upgrade_recipes,
        "plans_by_key": plans.PLANS,
        "pack_json": {p["id"]: json.loads(p["body_json"]) for p in packs},
        "pack_share_urls": {
            p["id"]: f"{config.BASE_URL}/share/{p['share_token']}"
            for p in packs if p["share_token"]
        },
        "shared_pack_id": shared_pack_id,
    })


@app.get("/w/{slug}/settings", response_class=HTMLResponse)
async def settings_page(request: Request, slug: str):
    org, user, member = _require_owner(request, slug)
    sub = billing.checkout_state(org)
    packs = db.all_("""SELECT id, title, status, share_token, created_at
                       FROM content_packs
                       WHERE org_id=? ORDER BY created_at DESC, id DESC""",
                    (org["id"],))
    shared_count = db.one("""SELECT COUNT(*) AS n FROM content_packs
                             WHERE org_id=? AND share_token IS NOT NULL""",
                          (org["id"],))["n"]
    bridge_count = db.one("""SELECT COUNT(*) AS n FROM content_packs
                             WHERE org_id=? AND status IN ('approved','exported')""",
                          (org["id"],))["n"]
    notice = request.query_params.get("notice", "")
    rotated = request.query_params.get("rotated", "") == "1"
    revoked = request.query_params.get("revoked", "")
    return templates.TemplateResponse(request, "settings.html", {
        "org": org,
        "user": user,
        "member": member,
        "subscription": sub,
        "packs": packs,
        "shared_count": shared_count,
        "bridge_count": bridge_count,
        "mise_bridge_armed": bool(config.MISE_IMPORT_TOKEN),
        "access_token_tail": org["access_token"][-6:],
        "notice": notice,
        "rotated": rotated,
        "revoked": revoked,
    })


@app.post("/w/{slug}/settings")
async def update_settings(request: Request, slug: str,
                          company: str = Form(""), email: str = Form(...),
                          market: str = Form(""), service_mix: str = Form(""),
                          brand_voice: str = Form("")):
    org, _, _ = _require_owner(request, slug)
    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="valid contact email required")
    db.run("""UPDATE organizations
              SET company=?, email=?, market=?, service_mix=?, brand_voice=?
              WHERE id=?""",
           (company.strip() or None, email, market.strip() or None,
            service_mix.strip() or None, brand_voice.strip() or None, org["id"]))
    return RedirectResponse(f"/w/{slug}/settings?notice=saved", status_code=303)


@app.post("/w/{slug}/settings/token")
async def rotate_access_token(request: Request, slug: str):
    org, _, _ = _require_owner(request, slug)
    db.run("""UPDATE organizations SET access_token=? WHERE id=?""",
           (security.new_token(), org["id"]))
    return RedirectResponse(f"/w/{slug}/settings?rotated=1", status_code=303)


@app.post("/w/{slug}/settings/packs/{pack_id}/revoke-share")
async def revoke_pack_share(request: Request, slug: str, pack_id: int):
    org, _, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    db.run("""UPDATE content_packs
              SET share_token=NULL, updated_at=datetime('now')
              WHERE id=?""", (pack_id,))
    return RedirectResponse(f"/w/{slug}/settings?revoked={pack_id}", status_code=303)


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


@app.post("/w/{slug}/packs/{pack_id}/approve")
async def approve_pack(request: Request, slug: str, pack_id: int):
    org, _ = _require_workspace(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    db.run("""UPDATE content_packs SET status='approved', approved_at=datetime('now'),
              updated_at=datetime('now') WHERE id=?""", (pack_id,))
    return RedirectResponse(f"/w/{slug}#pack-{pack_id}", status_code=303)


@app.post("/w/{slug}/packs/{pack_id}/share")
async def share_pack(request: Request, slug: str, pack_id: int):
    org, _ = _require_workspace(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    pack_utils.ensure_share_token(pack_id)
    return RedirectResponse(f"/w/{slug}?shared={pack_id}#pack-{pack_id}", status_code=303)


def _export_response(pack, fmt: str):
    if fmt == "md":
        content = pack_utils.markdown(pack)
        media_type = "text/markdown; charset=utf-8"
        ext = "md"
    elif fmt == "txt":
        content = pack_utils.plain_text(pack)
        media_type = "text/plain; charset=utf-8"
        ext = "txt"
    elif fmt == "json":
        content = json.dumps(pack_utils.body(pack), indent=2)
        media_type = "application/json"
        ext = "json"
    else:
        raise HTTPException(status_code=404, detail="unknown export format")
    db.run("""UPDATE content_packs SET status='exported', exported_at=datetime('now'),
              updated_at=datetime('now') WHERE id=?""", (pack["id"],))
    headers = {"Content-Disposition":
               f'attachment; filename="{pack_utils.filename(pack, ext)}"'}
    return Response(content, media_type=media_type, headers=headers)


@app.get("/w/{slug}/packs/{pack_id}/export.{fmt}")
async def export_pack(request: Request, slug: str, pack_id: int, fmt: str):
    org, _ = _require_workspace(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    return _export_response(pack, fmt)


@app.get("/share/{token}", response_class=HTMLResponse)
async def shared_pack(request: Request, token: str):
    pack = pack_utils.get_by_token(token)
    if not pack:
        raise HTTPException(status_code=404, detail="shared pack not found")
    return templates.TemplateResponse(request, "shared_pack.html", {
        "pack": pack,
        "body": pack_utils.body(pack),
        "markdown": pack_utils.markdown(pack),
    })


@app.get("/share/{token}/export.{fmt}")
async def export_shared_pack(token: str, fmt: str):
    pack = pack_utils.get_by_token(token)
    if not pack:
        raise HTTPException(status_code=404, detail="shared pack not found")
    return _export_response(pack, fmt)


@app.get("/share/{token}/copy.txt", response_class=PlainTextResponse)
async def copy_shared_pack(token: str):
    pack = pack_utils.get_by_token(token)
    if not pack:
        raise HTTPException(status_code=404, detail="shared pack not found")
    return pack_utils.plain_text(pack)


@app.get("/w/{slug}/billing", response_class=HTMLResponse)
async def billing_page(request: Request, slug: str):
    org, _ = _require_workspace(request, slug)
    suggested_plan = request.query_params.get("plan") or ""
    if suggested_plan not in plans.PLANS:
        suggested_plan = ""
    checkout_status = request.query_params.get("checkout", "")
    if checkout_status not in ("success", "cancel"):
        checkout_status = ""
    return templates.TemplateResponse(request, "billing.html", {
        "org": org,
        "subscription": billing.checkout_state(org),
        "plans": plans.all_plans(),
        "suggested_plan": suggested_plan,
        "checkout_status": checkout_status,
    })


@app.post("/w/{slug}/billing/plan")
async def choose_plan(request: Request, slug: str, plan: str = Form(...),
                      checkout: str = Form("")):
    org, _ = _require_workspace(request, slug)
    plan = plans.normalize_plan(plan, org["audience"])
    if plans.PLANS[plan]["audience"] != org["audience"]:
        raise HTTPException(status_code=400, detail="plan does not match workspace")
    db.run("UPDATE organizations SET plan=? WHERE id=?", (plan, org["id"]))
    billing.sync_trial_subscription(org["id"], plan)
    if checkout:
        fresh = _org_by_slug(slug)
        url = billing.create_checkout_session(
            fresh,
            success_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=success",
            cancel_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=cancel",
        )
        return RedirectResponse(url, status_code=303)
    return RedirectResponse(f"/w/{slug}/billing", status_code=303)


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


def _pack_api_payload(pack) -> dict:
    token = pack["share_token"] or pack_utils.ensure_share_token(pack["id"])
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
        "share_url": f"{config.BASE_URL}/share/{token}",
        "markdown": pack_utils.markdown(pack),
        "body": pack_utils.body(pack),
    }


@app.get("/api/mise/organizations/{slug}/packs",
         dependencies=[Depends(security.require_mise_token)])
async def packs_for_mise(slug: str, include_drafts: bool = False):
    org = _org_by_slug(slug)
    where = "cp.org_id=?"
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
    return {
        "matched": True,
        "org": slug,
        "packs": [_pack_api_payload(p) for p in rows],
    }


@app.get("/api/mise/organizations/{slug}/latest-pack",
         dependencies=[Depends(security.require_mise_token)])
async def latest_pack_for_mise(slug: str, include_drafts: bool = False):
    org = _org_by_slug(slug)
    where = "cp.org_id=?"
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
