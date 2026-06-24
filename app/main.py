"""Dionysus / Platekit — Photography AI SaaS for restaurants and photographers."""

from contextlib import asynccontextmanager
import csv
import datetime as dt
import io
import json
import logging
from urllib.parse import quote, urlencode, urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (
    audit, billing, config, db, generator, jobs, mise_hook, packs as pack_utils,
    plans, readiness, recipes, security,
)
from .render import ROOT, templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    yield


app = FastAPI(title="Dionysus", version="0.2.0", docs_url=None,
              redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


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
    return len(parts) >= 3 and parts[0] == "w" and parts[2] in {
        "billing", "settings", "support"}


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


@app.get("/invite/{token}", response_class=HTMLResponse)
async def invite_form(request: Request, token: str):
    invite = _invite_by_token(token)
    if not invite or invite["status"] != "pending":
        raise HTTPException(status_code=404, detail="invite not found")
    return _render_invite(request, invite)


@app.post("/invite/{token}/accept")
async def accept_invite(request: Request, token: str, name: str = Form(""),
                        password: str = Form(...)):
    invite = _invite_by_token(token)
    if not invite or invite["status"] != "pending":
        raise HTTPException(status_code=404, detail="invite not found")
    password = password.strip()
    if len(password) < 8:
        return _render_invite(
            request, invite, error="Password must be at least 8 characters.")
    user = db.one("SELECT * FROM users WHERE email=?", (invite["email"],))
    if user:
        if not security.verify_password(password, user["password_hash"]):
            return _render_invite(
                request, invite, error="Use the password for this account.")
        user_id = user["id"]
    else:
        display_name = name.strip() or invite["invitee_name"] or invite["email"].split("@", 1)[0]
        user_id = db.run("""INSERT INTO users (email, name, password_hash)
                            VALUES (?,?,?)""",
                         (invite["email"], display_name, security.hash_password(password)))
    with db.tx() as con:
        con.execute("""INSERT INTO organization_members (org_id, user_id, role)
                       VALUES (?,?,?)
                       ON CONFLICT(org_id, user_id) DO UPDATE SET
                         role=excluded.role""",
                    (invite["org_id"], user_id, invite["role"]))
        con.execute("""UPDATE workspace_invites
                       SET status='accepted', accepted_by_user_id=?,
                           accepted_at=datetime('now')
                       WHERE id=?""", (user_id, invite["id"]))
    audit.log_event(
        invite["org_id"], "member.invite_accepted",
        actor_user_id=user_id, entity_type="user", entity_id=user_id,
        summary=f"{invite['email']} accepted a {invite['role']} invite.",
        details={"email": invite["email"], "role": invite["role"]})
    resp = RedirectResponse(f"/w/{invite['org_slug']}", status_code=303)
    return _set_auth_cookies(resp, user_id, invite["org_slug"])


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
    if not user:
        raise HTTPException(status_code=403, detail="workspace access required")
    if _membership(user["id"], org["id"]):
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


def _member_role(role: str) -> str:
    role = role.strip().lower()
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role must be admin or member")
    return role


def _valid_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="valid email required")
    return email


def _owner_count(org_id: int) -> int:
    return db.one("""SELECT COUNT(*) AS n FROM organization_members
                     WHERE org_id=? AND role='owner'""", (org_id,))["n"]


def _new_invite_token() -> str:
    token = security.new_token(32)
    while db.one("SELECT id FROM workspace_invites WHERE token=?", (token,)):
        token = security.new_token(32)
    return token


def _invite_url(token: str) -> str:
    return f"{config.BASE_URL}/invite/{token}"


def _invite_by_token(token: str):
    return db.one("""SELECT wi.*, o.slug AS org_slug, o.name AS org_name,
                            o.company AS org_company
                     FROM workspace_invites wi
                     JOIN organizations o ON o.id=wi.org_id
                     WHERE wi.token=?""", (token,))


def _render_invite(request: Request, invite, *, error: str | None = None):
    org = {
        "id": invite["org_id"],
        "slug": invite["org_slug"],
        "name": invite["org_name"],
        "company": invite["org_company"],
    }
    existing_user = db.one("SELECT id FROM users WHERE email=?", (invite["email"],))
    return templates.TemplateResponse(request, "accept_invite.html", {
        "invite": invite,
        "org": org,
        "existing_user": bool(existing_user),
        "error": error,
    })


def _audit_filters(request: Request) -> dict:
    filters = {
        "action": request.query_params.get("audit_action", "").strip(),
        "actor": request.query_params.get("audit_actor", "").strip(),
        "date_from": request.query_params.get("audit_from", "").strip(),
        "date_to": request.query_params.get("audit_to", "").strip(),
    }
    for key in ("date_from", "date_to"):
        if filters[key]:
            try:
                dt.date.fromisoformat(filters[key])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="audit date must be YYYY-MM-DD") from exc
    return filters


def _audit_query(filters: dict) -> dict:
    return {
        "audit_action": filters["action"],
        "audit_actor": filters["actor"],
        "audit_from": filters["date_from"],
        "audit_to": filters["date_to"],
    }


def _audit_export_url(slug: str, fmt: str, filters: dict) -> str:
    query = {key: value for key, value in _audit_query(filters).items() if value}
    suffix = f"?{urlencode(query)}" if query else ""
    return f"/w/{slug}/settings/audit/export.{fmt}{suffix}"


def _count_by(rows, key: str, defaults: tuple[str, ...]) -> dict:
    counts = {value: 0 for value in defaults}
    for row in rows:
        counts[row[key]] = row["n"]
    return counts


def _set_auth_cookies(resp: RedirectResponse, user_id: int, slug: str) -> RedirectResponse:
    resp.set_cookie(security.USER_COOKIE, security.user_cookie(user_id),
                    **_auth_cookie_kwargs())
    resp.set_cookie(security.WORKSPACE_COOKIE, security.workspace_cookie(slug),
                    **_auth_cookie_kwargs())
    return resp


def _enforce_monthly_pack_limit(org, sub: dict | None = None) -> None:
    sub = sub or billing.checkout_state(org)
    limit = plans.pack_limit(sub["plan"])
    if limit is None:
        return
    period = dt.date.today().strftime("%Y-%m")
    used = db.one("""SELECT COUNT(*) AS n FROM content_packs
                     WHERE org_id=? AND substr(created_at,1,7)=?""",
                  (org["id"], period))["n"]
    if used >= limit:
        raise HTTPException(status_code=402, detail="monthly pack limit reached")


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
    show_archived = request.query_params.get("archived", "") == "1"
    pack_where = "cp.org_id=?"
    if not show_archived:
        pack_where += " AND cp.archived_at IS NULL"
    packs = db.all_(f"""SELECT cp.*, cr.name AS recipe_name,
                               sp.title AS source_pack_title
                        FROM content_packs cp
                        JOIN content_recipes cr ON cr.id=cp.recipe_id
                        LEFT JOIN content_packs sp ON sp.id=cp.source_pack_id
                        WHERE {pack_where}
                        ORDER BY CASE
                                   WHEN cp.archived_at IS NOT NULL THEN 2
                                   WHEN cp.status='draft' THEN 0
                                   ELSE 1
                                 END,
                                 cp.created_at DESC, cp.id DESC""",
                    (org["id"],))
    latest_pack = next((p for p in packs if not p["archived_at"]), None)
    shared_pack_raw = request.query_params.get("shared", "")
    try:
        shared_pack_id = int(shared_pack_raw)
    except ValueError:
        shared_pack_id = 0
    regenerated_pack_raw = request.query_params.get("regenerated", "")
    try:
        regenerated_pack_id = int(regenerated_pack_raw)
    except ValueError:
        regenerated_pack_id = 0
    archived_count = db.one("""SELECT COUNT(*) AS n FROM content_packs
                              WHERE org_id=? AND archived_at IS NOT NULL""",
                            (org["id"],))["n"]
    sub = billing.checkout_state(org)
    current_member = _membership(user["id"], org["id"]) if user else None
    can_manage_workspace = bool(
        current_member and current_member["role"] in ("owner", "admin"))
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
        "show_archived": show_archived, "archived_count": archived_count,
        "limit_upgrade_plan": limit_upgrade_plan,
        "upgrade_recipes": upgrade_recipes,
        "plans_by_key": plans.PLANS,
        "pack_json": {p["id"]: json.loads(p["body_json"]) for p in packs},
        "pack_share_urls": {
            p["id"]: f"{config.BASE_URL}/share/{p['share_token']}"
            for p in packs if p["share_token"]
        },
        "shared_pack_id": shared_pack_id,
        "regenerated_pack_id": regenerated_pack_id,
        "can_manage_workspace": can_manage_workspace,
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
    members = db.all_("""SELECT om.user_id, om.role, om.created_at,
                                u.name, u.email
                         FROM organization_members om
                         JOIN users u ON u.id=om.user_id
                         WHERE om.org_id=?
                         ORDER BY CASE om.role WHEN 'owner' THEN 0
                                                WHEN 'admin' THEN 1 ELSE 2 END,
                                  om.created_at""", (org["id"],))
    pending_invites = []
    for row in db.all_("""SELECT * FROM workspace_invites
                          WHERE org_id=? AND status='pending'
                          ORDER BY created_at DESC, id DESC""", (org["id"],)):
        invite = dict(row)
        invite["invite_url"] = _invite_url(invite["token"])
        pending_invites.append(invite)
    invited_raw = request.query_params.get("invited", "")
    invite_url = ""
    try:
        invited_id = int(invited_raw)
    except ValueError:
        invited_id = 0
    if invited_id:
        invite = db.one("""SELECT token FROM workspace_invites
                           WHERE id=? AND org_id=? AND status='pending'""",
                        (invited_id, org["id"]))
        if invite:
            invite_url = _invite_url(invite["token"])
    audit_filters = _audit_filters(request)
    return templates.TemplateResponse(request, "settings.html", {
        "org": org,
        "user": user,
        "member": member,
        "subscription": sub,
        "packs": packs,
        "members": members,
        "pending_invites": pending_invites,
        "invite_url": invite_url,
        "shared_count": shared_count,
        "bridge_count": bridge_count,
        "mise_bridge_armed": bool(config.MISE_IMPORT_TOKEN),
        "access_token_tail": org["access_token"][-6:],
        "audit_events": audit.for_org(org["id"], **audit_filters),
        "audit_actions": audit.actions_for_org(org["id"]),
        "audit_actors": audit.actors_for_org(org["id"]),
        "audit_filters": audit_filters,
        "audit_export_csv_url": _audit_export_url(slug, "csv", audit_filters),
        "audit_export_json_url": _audit_export_url(slug, "json", audit_filters),
        "notice": notice,
        "rotated": rotated,
        "revoked": revoked,
    })



@app.get("/w/{slug}/support", response_class=HTMLResponse)
async def support_dashboard(request: Request, slug: str):
    org, user, member = _require_owner(request, slug)
    sub = billing.checkout_state(org)
    members = db.all_("""SELECT om.user_id, om.role, om.created_at,
                                u.name, u.email, u.last_login_at
                         FROM organization_members om
                         JOIN users u ON u.id=om.user_id
                         WHERE om.org_id=?
                         ORDER BY CASE om.role WHEN 'owner' THEN 0
                                                WHEN 'admin' THEN 1 ELSE 2 END,
                                  u.email""", (org["id"],))
    member_counts = _count_by(
        db.all_("""SELECT role, COUNT(*) AS n FROM organization_members
                   WHERE org_id=? GROUP BY role""", (org["id"],)),
        "role", ("owner", "admin", "member"))
    invite_counts = _count_by(
        db.all_("""SELECT status, COUNT(*) AS n FROM workspace_invites
                   WHERE org_id=? GROUP BY status""", (org["id"],)),
        "status", ("pending", "accepted", "revoked"))
    pack_counts = _count_by(
        db.all_("""SELECT status, COUNT(*) AS n FROM content_packs
                   WHERE org_id=? GROUP BY status""", (org["id"],)),
        "status", ("draft", "approved", "exported"))
    recent_invites = db.all_("""SELECT wi.*, inviter.email AS invited_by_email,
                                       accepter.email AS accepted_by_email
                                FROM workspace_invites wi
                                LEFT JOIN users inviter ON inviter.id=wi.invited_by_user_id
                                LEFT JOIN users accepter ON accepter.id=wi.accepted_by_user_id
                                WHERE wi.org_id=?
                                ORDER BY wi.created_at DESC, wi.id DESC
                                LIMIT 8""", (org["id"],))
    latest_pack = db.one("""SELECT id, title, status, share_token, created_at
                            FROM content_packs
                            WHERE org_id=?
                            ORDER BY created_at DESC, id DESC
                            LIMIT 1""", (org["id"],))
    shared_count = db.one("""SELECT COUNT(*) AS n FROM content_packs
                             WHERE org_id=? AND share_token IS NOT NULL""",
                          (org["id"],))["n"]
    audit_count = db.one("SELECT COUNT(*) AS n FROM audit_events WHERE org_id=?",
                         (org["id"],))["n"]
    return templates.TemplateResponse(request, "support.html", {
        "org": org,
        "user": user,
        "member": member,
        "subscription": sub,
        "members": members,
        "member_total": len(members),
        "member_counts": member_counts,
        "invite_counts": invite_counts,
        "pack_total": sum(pack_counts.values()),
        "pack_counts": pack_counts,
        "recent_invites": recent_invites,
        "latest_pack": latest_pack,
        "shared_count": shared_count,
        "audit_count": audit_count,
        "audit_events": audit.recent_for_org(org["id"], limit=8),
        "jobs_pending": jobs.pending_count(),
        "mise_bridge_armed": bool(config.MISE_IMPORT_TOKEN),
        "access_token_tail": org["access_token"][-6:],
    })


@app.get("/w/{slug}/settings/audit/events/{event_id}", response_class=HTMLResponse)
async def audit_event_page(request: Request, slug: str, event_id: int):
    org, _, _ = _require_owner(request, slug)
    event = audit.get_for_org(org["id"], event_id)
    if not event:
        raise HTTPException(status_code=404, detail="audit event not found")
    return templates.TemplateResponse(request, "audit_event.html", {
        "org": org,
        "event": event,
        "details_json": json.dumps(event["details"], indent=2, sort_keys=True),
    })


@app.get("/w/{slug}/settings/audit/export.{fmt}")
async def export_audit(request: Request, slug: str, fmt: str):
    org, _, _ = _require_owner(request, slug)
    filters = _audit_filters(request)
    events = audit.for_org(org["id"], **filters, limit=5000)
    filename = f"{slug}-audit.{fmt}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if fmt == "json":
        content = json.dumps(events, indent=2)
        return Response(content, media_type="application/json", headers=headers)
    if fmt == "csv":
        output = io.StringIO()
        fields = [
            "created_at", "actor", "action", "entity_type", "entity_id",
            "summary", "details_json",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for event in events:
            writer.writerow({
                "created_at": event["created_at"],
                "actor": event["actor_label"],
                "action": event["action"],
                "entity_type": event["entity_type"] or "",
                "entity_id": event["entity_id"] or "",
                "summary": event["summary"],
                "details_json": event["details_json"],
            })
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers=headers)
    raise HTTPException(status_code=404, detail="unknown export format")


@app.post("/w/{slug}/settings")
async def update_settings(request: Request, slug: str,
                          company: str = Form(""), email: str = Form(...),
                          market: str = Form(""), service_mix: str = Form(""),
                          brand_voice: str = Form("")):
    org, user, _ = _require_owner(request, slug)
    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="valid contact email required")
    updates = {
        "company": company.strip() or None,
        "email": email,
        "market": market.strip() or None,
        "service_mix": service_mix.strip() or None,
        "brand_voice": brand_voice.strip() or None,
    }
    labels = {
        "company": "business/studio",
        "email": "contact email",
        "market": "market",
        "service_mix": "service mix",
        "brand_voice": "brand voice",
    }
    changed = [labels[key] for key, value in updates.items() if org[key] != value]
    db.run("""UPDATE organizations
              SET company=?, email=?, market=?, service_mix=?, brand_voice=?
              WHERE id=?""",
           (updates["company"], updates["email"], updates["market"],
            updates["service_mix"], updates["brand_voice"], org["id"]))
    summary = "Updated workspace basics"
    if changed:
        summary = f"{summary}: {', '.join(changed)}."
    else:
        summary = f"{summary}: no field changes."
    audit.log_event(
        org["id"], "workspace.settings_updated",
        actor_user_id=user["id"], entity_type="organization", entity_id=org["id"],
        summary=summary, details={"fields": changed})
    return RedirectResponse(f"/w/{slug}/settings?notice=saved", status_code=303)


@app.post("/w/{slug}/settings/token")
async def rotate_access_token(request: Request, slug: str):
    org, user, _ = _require_owner(request, slug)
    token = security.new_token()
    db.run("""UPDATE organizations SET access_token=? WHERE id=?""",
           (token, org["id"]))
    audit.log_event(
        org["id"], "workspace.token_rotated",
        actor_user_id=user["id"], entity_type="organization", entity_id=org["id"],
        summary=f"Rotated workspace token; new token ends in {token[-6:]}.",
        details={"token_tail": token[-6:]})
    return RedirectResponse(f"/w/{slug}/settings?rotated=1", status_code=303)


@app.post("/w/{slug}/settings/packs/{pack_id}/revoke-share")
async def revoke_pack_share(request: Request, slug: str, pack_id: int):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    db.run("""UPDATE content_packs
              SET share_token=NULL, updated_at=datetime('now')
              WHERE id=?""", (pack_id,))
    audit.log_event(
        org["id"], "pack.share_revoked",
        actor_user_id=user["id"], entity_type="content_pack", entity_id=pack_id,
        summary=f"Revoked public share link for {pack['title']}.",
        details={"pack_title": pack["title"]})
    return RedirectResponse(f"/w/{slug}/settings?revoked={pack_id}", status_code=303)


@app.post("/w/{slug}/settings/members/invite")
async def invite_member(request: Request, slug: str, email: str = Form(...),
                        role: str = Form("member"), invitee_name: str = Form("")):
    org, user, _ = _require_owner(request, slug)
    email = _valid_email(email)
    role = _member_role(role)
    invitee_name = invitee_name.strip() or None
    existing_member = db.one("""SELECT om.user_id FROM organization_members om
                                JOIN users u ON u.id=om.user_id
                                WHERE om.org_id=? AND u.email=?""",
                             (org["id"], email))
    if existing_member:
        raise HTTPException(status_code=400, detail="user is already a workspace member")
    pending = db.one("""SELECT * FROM workspace_invites
                        WHERE org_id=? AND email=? AND status='pending'""",
                     (org["id"], email))
    if pending:
        db.run("""UPDATE workspace_invites
                  SET role=?, invitee_name=?, invited_by_user_id=?
                  WHERE id=?""", (role, invitee_name, user["id"], pending["id"]))
        invite_id = pending["id"]
        summary = f"Refreshed {role} invite for {email}."
    else:
        token = _new_invite_token()
        invite_id = db.run("""INSERT INTO workspace_invites
                              (org_id, email, invitee_name, role, token, invited_by_user_id)
                              VALUES (?,?,?,?,?,?)""",
                           (org["id"], email, invitee_name, role, token, user["id"]))
        summary = f"Invited {email} as {role}."
    audit.log_event(
        org["id"], "member.invited",
        actor_user_id=user["id"], entity_type="workspace_invite", entity_id=invite_id,
        summary=summary, details={"email": email, "role": role})
    return RedirectResponse(f"/w/{slug}/settings?invited={invite_id}", status_code=303)


@app.post("/w/{slug}/settings/invites/{invite_id}/revoke")
async def revoke_invite(request: Request, slug: str, invite_id: int):
    org, user, _ = _require_owner(request, slug)
    invite = db.one("""SELECT * FROM workspace_invites
                       WHERE id=? AND org_id=? AND status='pending'""",
                    (invite_id, org["id"]))
    if not invite:
        raise HTTPException(status_code=404, detail="invite not found")
    db.run("""UPDATE workspace_invites
              SET status='revoked', revoked_at=datetime('now')
              WHERE id=?""", (invite_id,))
    audit.log_event(
        org["id"], "member.invite_revoked",
        actor_user_id=user["id"], entity_type="workspace_invite", entity_id=invite_id,
        summary=f"Revoked pending invite for {invite['email']}.",
        details={"email": invite["email"], "role": invite["role"]})
    return RedirectResponse(f"/w/{slug}/settings?invite_revoked=1", status_code=303)


@app.post("/w/{slug}/settings/members/{user_id}/role")
async def update_member_role(request: Request, slug: str, user_id: int,
                             role: str = Form(...)):
    org, user, _ = _require_owner(request, slug)
    role = _member_role(role)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="cannot change your own role")
    target = db.one("""SELECT om.*, u.email FROM organization_members om
                       JOIN users u ON u.id=om.user_id
                       WHERE om.org_id=? AND om.user_id=?""", (org["id"], user_id))
    if not target:
        raise HTTPException(status_code=404, detail="member not found")
    if target["role"] == "owner":
        raise HTTPException(status_code=400, detail="owner role cannot be changed here")
    db.run("""UPDATE organization_members SET role=?
              WHERE org_id=? AND user_id=?""", (role, org["id"], user_id))
    audit.log_event(
        org["id"], "member.role_updated",
        actor_user_id=user["id"], entity_type="user", entity_id=user_id,
        summary=f"Changed {target['email']} from {target['role']} to {role}.",
        details={"email": target["email"], "old_role": target["role"], "role": role})
    return RedirectResponse(f"/w/{slug}/settings?member_role=1", status_code=303)


@app.post("/w/{slug}/settings/members/{user_id}/revoke")
async def revoke_member(request: Request, slug: str, user_id: int):
    org, user, _ = _require_owner(request, slug)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="cannot revoke your own access")
    target = db.one("""SELECT om.*, u.email FROM organization_members om
                       JOIN users u ON u.id=om.user_id
                       WHERE om.org_id=? AND om.user_id=?""", (org["id"], user_id))
    if not target:
        raise HTTPException(status_code=404, detail="member not found")
    if target["role"] == "owner" and _owner_count(org["id"]) <= 1:
        raise HTTPException(status_code=400, detail="cannot revoke the last owner")
    db.run("""DELETE FROM organization_members
              WHERE org_id=? AND user_id=?""", (org["id"], user_id))
    audit.log_event(
        org["id"], "member.revoked",
        actor_user_id=user["id"], entity_type="user", entity_id=user_id,
        summary=f"Revoked workspace access for {target['email']}.",
        details={"email": target["email"], "role": target["role"]})
    return RedirectResponse(f"/w/{slug}/settings?member_revoked=1", status_code=303)


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
                        recipe_id: int = Form(...),
                        argus_run_id: int | None = Form(None)):
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
    _enforce_monthly_pack_limit(org, sub=sub)
    jobs.enqueue_generate(campaign_id, recipe_id, argus_run_id=argus_run_id)
    return RedirectResponse(f"/w/{slug}#packs", status_code=303)


def _revision_lines(raw: str) -> list[str]:
    lines = []
    for line in raw.splitlines():
        clean = line.strip()
        if clean.startswith("- "):
            clean = clean[2:].strip()
        if clean:
            lines.append(clean)
    return lines


def _require_publishable_pack(pack) -> None:
    if pack["status"] == "draft":
        raise HTTPException(status_code=400, detail="approve pack before publishing")


def _require_active_pack(pack) -> None:
    if pack["archived_at"]:
        raise HTTPException(status_code=400, detail="archived packs are read-only")


@app.post("/w/{slug}/packs/{pack_id}/revise")
async def revise_pack(request: Request, slug: str, pack_id: int,
                      title: str = Form(...), strategy: str = Form(...),
                      shot_list: str = Form(""), captions: str = Form(""),
                      exports: str = Form(""), upsells: str = Form("")):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    _require_active_pack(pack)
    if pack["status"] != "draft":
        raise HTTPException(status_code=400, detail="only draft packs can be revised")
    title = title.strip()
    strategy = strategy.strip()
    if not title or not strategy:
        raise HTTPException(status_code=400, detail="title and strategy are required")
    revised = pack_utils.body(pack)
    revised["headline"] = title
    revised["strategy"] = strategy
    revised["shot_list"] = _revision_lines(shot_list)
    revised["captions"] = _revision_lines(captions)
    revised["exports"] = _revision_lines(exports)
    revised["upsells"] = _revision_lines(upsells)
    if not any(revised[key] for key in ("shot_list", "captions", "exports", "upsells")):
        raise HTTPException(status_code=400, detail="at least one pack section is required")
    db.run("""UPDATE content_packs
              SET title=?, body_json=?, updated_at=datetime('now')
              WHERE id=?""", (title, json.dumps(revised), pack_id))
    audit.log_event(
        org["id"], "pack.revised",
        actor_user_id=audit.actor_id(user), entity_type="content_pack", entity_id=pack_id,
        summary=f"Revised draft pack: {title}.",
        details={
            "previous_title": pack["title"],
            "pack_title": title,
            "section_counts": {
                "shot_list": len(revised["shot_list"]),
                "captions": len(revised["captions"]),
                "exports": len(revised["exports"]),
                "upsells": len(revised["upsells"]),
            },
        })
    return RedirectResponse(f"/w/{slug}?revised={pack_id}#pack-{pack_id}", status_code=303)


@app.post("/w/{slug}/packs/{pack_id}/regenerate")
async def regenerate_pack(request: Request, slug: str, pack_id: int,
                          feedback: str = Form(...)):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    _require_active_pack(pack)
    feedback = feedback.strip()
    if len(feedback) < 4:
        raise HTTPException(status_code=400, detail="feedback is required")
    sub = billing.checkout_state(org)
    if not plans.allowed_recipe(sub["plan"], pack["recipe_slug"]):
        raise HTTPException(status_code=402, detail="upgrade required for this recipe")
    _enforce_monthly_pack_limit(org, sub=sub)
    regenerated = generator.regenerate_with_feedback(org, pack, feedback)
    engine = regenerated["provenance"]["engine"]
    new_pack_id = db.run("""INSERT INTO content_packs
                            (org_id, campaign_id, recipe_id, title, body_json,
                             ai_model, ai_draft_original, source_pack_id,
                             revision_note)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                         (org["id"], pack["campaign_id"], pack["recipe_id"],
                          regenerated["headline"], json.dumps(regenerated),
                          engine, json.dumps(regenerated), pack["id"], feedback))
    audit.log_event(
        org["id"], "pack.regenerated",
        actor_user_id=audit.actor_id(user), entity_type="content_pack", entity_id=new_pack_id,
        summary=f"Regenerated {pack['title']} into a new draft.",
        details={
            "source_pack_id": pack["id"],
            "source_status": pack["status"],
            "pack_title": regenerated["headline"],
            "feedback": feedback,
        })
    return RedirectResponse(
        f"/w/{slug}?regenerated={new_pack_id}#pack-{new_pack_id}", status_code=303)


@app.post("/w/{slug}/packs/{pack_id}/archive")
async def archive_pack(request: Request, slug: str, pack_id: int):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    if pack["archived_at"]:
        return RedirectResponse(f"/w/{slug}?archived=1#pack-{pack_id}", status_code=303)
    if pack["status"] != "draft":
        raise HTTPException(status_code=400, detail="only draft packs can be archived")
    db.run("""UPDATE content_packs
              SET archived_at=datetime('now'), updated_at=datetime('now')
              WHERE id=?""", (pack_id,))
    audit.log_event(
        org["id"], "pack.archived",
        actor_user_id=audit.actor_id(user), entity_type="content_pack", entity_id=pack_id,
        summary=f"Archived draft pack: {pack['title']}.",
        details={
            "pack_title": pack["title"],
            "source_pack_id": pack["source_pack_id"],
        })
    return RedirectResponse(f"/w/{slug}?archived=1#pack-{pack_id}", status_code=303)


@app.post("/w/{slug}/packs/{pack_id}/approve")
async def approve_pack(request: Request, slug: str, pack_id: int):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    _require_active_pack(pack)
    db.run("""UPDATE content_packs SET status='approved', approved_at=datetime('now'),
              updated_at=datetime('now') WHERE id=?""", (pack_id,))
    audit.log_event(
        org["id"], "pack.approved",
        actor_user_id=audit.actor_id(user), entity_type="content_pack", entity_id=pack_id,
        summary=f"Approved pack: {pack['title']}.",
        details={"pack_title": pack["title"]})
    return RedirectResponse(f"/w/{slug}#pack-{pack_id}", status_code=303)


@app.post("/w/{slug}/packs/{pack_id}/share")
async def share_pack(request: Request, slug: str, pack_id: int):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    _require_active_pack(pack)
    _require_publishable_pack(pack)
    had_token = bool(pack["share_token"])
    token = pack_utils.ensure_share_token(pack_id)
    verb = "Reused" if had_token else "Created"
    audit.log_event(
        org["id"], "pack.share_enabled",
        actor_user_id=audit.actor_id(user), entity_type="content_pack", entity_id=pack_id,
        summary=f"{verb} public share link for {pack['title']}.",
        details={
            "created": not had_token,
            "pack_title": pack["title"],
            "share_token_tail": token[-6:],
        })
    return RedirectResponse(f"/w/{slug}?shared={pack_id}#pack-{pack_id}", status_code=303)


def _export_response(pack, fmt: str, *, mark_exported: bool = True):
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
    if mark_exported:
        db.run("""UPDATE content_packs SET status='exported', exported_at=datetime('now'),
                  updated_at=datetime('now') WHERE id=?""", (pack["id"],))
    headers = {"Content-Disposition":
               f'attachment; filename="{pack_utils.filename(pack, ext)}"'}
    return Response(content, media_type=media_type, headers=headers)


@app.get("/w/{slug}/packs/{pack_id}/export.{fmt}")
async def export_pack(request: Request, slug: str, pack_id: int, fmt: str):
    org, user, _ = _require_owner(request, slug)
    pack = pack_utils.get_for_org(pack_id, org["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    _require_active_pack(pack)
    _require_publishable_pack(pack)
    resp = _export_response(pack, fmt)
    audit.log_event(
        org["id"], "pack.exported",
        actor_user_id=audit.actor_id(user), entity_type="content_pack", entity_id=pack_id,
        summary=f"Exported {pack['title']} as {fmt}.",
        details={"pack_title": pack["title"], "format": fmt})
    return resp


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
    return _export_response(pack, fmt, mark_exported=False)


@app.get("/share/{token}/copy.txt", response_class=PlainTextResponse)
async def copy_shared_pack(token: str):
    pack = pack_utils.get_by_token(token)
    if not pack:
        raise HTTPException(status_code=404, detail="shared pack not found")
    return pack_utils.plain_text(pack)


@app.get("/w/{slug}/billing", response_class=HTMLResponse)
async def billing_page(request: Request, slug: str):
    org, _, _ = _require_owner(request, slug)
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
    org, user, _ = _require_owner(request, slug)
    plan = plans.normalize_plan(plan, org["audience"])
    if plans.PLANS[plan]["audience"] != org["audience"]:
        raise HTTPException(status_code=400, detail="plan does not match workspace")
    db.run("UPDATE organizations SET plan=? WHERE id=?", (plan, org["id"]))
    billing.sync_trial_subscription(org["id"], plan)
    audit.log_event(
        org["id"], "billing.plan_selected",
        actor_user_id=audit.actor_id(user), entity_type="subscription", entity_id=org["id"],
        summary=f"Selected {plans.PLANS[plan]['name']} plan.",
        details={"plan": plan, "previous_plan": org["plan"]})
    if checkout:
        fresh = _org_by_slug(slug)
        url = billing.create_checkout_session(
            fresh,
            success_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=success",
            cancel_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=cancel",
        )
        audit.log_event(
            org["id"], "billing.checkout_started",
            actor_user_id=audit.actor_id(user), entity_type="subscription", entity_id=org["id"],
            summary=f"Started Stripe checkout for {plans.PLANS[plan]['name']}.",
            details={"plan": plan})
        return RedirectResponse(url, status_code=303)
    return RedirectResponse(f"/w/{slug}/billing", status_code=303)


@app.post("/w/{slug}/billing/checkout")
async def start_checkout(request: Request, slug: str):
    org, user, _ = _require_owner(request, slug)
    url = billing.create_checkout_session(
        org,
        success_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=success",
        cancel_url=f"{config.BASE_URL}/w/{slug}/billing?checkout=cancel",
    )
    state = billing.checkout_state(org)
    audit.log_event(
        org["id"], "billing.checkout_started",
        actor_user_id=audit.actor_id(user), entity_type="subscription", entity_id=org["id"],
        summary=f"Started Stripe checkout for {state['plan_meta']['name']}.",
        details={"plan": state["plan"]})
    return RedirectResponse(url, status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    event = await billing.construct_webhook_event(request)
    return billing.handle_event(event)


def _pack_api_payload(pack) -> dict:
    share_url = None
    if pack["share_token"]:
        share_url = f"{config.BASE_URL}/share/{pack['share_token']}"
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
        "share_url": share_url,
        "source_pack_id": pack["source_pack_id"],
        "revision_note": pack["revision_note"],
        "archived_at": pack["archived_at"],
        "markdown": pack_utils.markdown(pack),
        "body": pack_utils.body(pack),
    }


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
    return {
        "matched": True,
        "org": slug,
        "packs": [_pack_api_payload(p) for p in rows],
    }


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


@app.post("/api/mise/organizations/{slug}/print-pitch",
          dependencies=[Depends(security.require_mise_token)])
async def mise_print_pitch(slug: str, request: Request):
    """Plutus hand-off — enrich client print upsell email copy from bundle rationale."""
    org = _org_by_slug(slug)
    if not org:
        raise HTTPException(status_code=404, detail="organization not found")
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

    return {
        "ok": True,
        "org": slug,
        **print_pitch.build_print_pitch(
            gallery_name=gallery_name.strip(),
            bundles=bundles,
            photo_count=photo_count,
            estimated_total_cents=estimated_total_cents,
            gallery_theme=(gallery_theme or "").strip() or None,
            argus_run_id=argus_run_id,
        ),
    }


@app.post("/api/mise/organizations/{slug}/argus-pack",
          dependencies=[Depends(security.require_mise_token)])
async def mise_argus_pack_hook(slug: str, request: Request):
    """Mise Argus callback hook — draft a keyword-enriched pack from one run."""
    org = _org_by_slug(slug)
    if not org:
        raise HTTPException(status_code=404, detail="organization not found")
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
    try:
        result = mise_hook.generate_from_argus(
            org,
            argus_run_id=argus_run_id,
            mise_gallery_id=mise_gallery_id,
            gallery_title=gallery_title,
            recipe_slug=(recipe_slug or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "org": slug, **result}
