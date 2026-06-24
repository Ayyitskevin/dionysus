import json
import os
import sqlite3
import stat

from fastapi.testclient import TestClient

os.environ["DIONYSUS_DATA_DIR"] = "/tmp/dionysus-test-data"
os.environ["DIONYSUS_SECRET_KEY"] = "test-secret"
os.environ["DIONYSUS_MISE_IMPORT_TOKEN"] = "mise-test"

from app import db, generator, jobs, security  # noqa: E402
from app.main import app  # noqa: E402


def configure_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONYSUS_DATA_DIR", str(tmp_path))
    from app import config
    config.DATA_DIR = tmp_path
    config.DB_PATH = tmp_path / "dionysus.db"
    config.MISE_IMPORT_TOKEN = "mise-test"
    config.BASE_URL = "http://localhost:8450"
    config.COOKIE_SECURE = False
    config.STRIPE_SECRET_KEY = ""
    config.STRIPE_PRICE_RESTAURANT_STARTER = ""
    config.STRIPE_PRICE_RESTAURANT_GROWTH = ""
    config.STRIPE_PRICE_PHOTOGRAPHER_STUDIO = ""
    db.migrate()


def signup(client, *, drain=True, **overrides):
    data = {
        "name": "Avery",
        "email": "avery@example.com",
        "password": "correct-horse",
        "company": "Blue Plate",
        "audience": "restaurant",
        "plan": "restaurant_growth",
        "market": "Asheville",
        "brand_voice": "warm and chef-led",
        "service_mix": "dine-in and delivery",
        "first_item": "Spring agnolotti",
        "first_item_note": "peas, ricotta, lemon",
        "campaign_goal": "fill weekday reservations",
        "launch_date": "2026-07-01",
    }
    data.update(overrides)
    res = client.post("/signup", data=data, follow_redirects=False)
    if drain:
        jobs.drain()
    return res


def test_home_prefills_signup_from_query_params(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get("/?name=Avery&email=avery%40example.com&company=Blue+Plate&audience=restaurant&plan=restaurant_growth&market=Asheville&first_item=Spring+agnolotti")
    assert res.status_code == 200
    assert 'name="name" value="Avery"' in res.text
    assert 'name="email" type="email" value="avery@example.com"' in res.text
    assert 'name="company" value="Blue Plate"' in res.text
    assert 'value="restaurant_growth" data-audience="restaurant" selected' in res.text
    assert 'name="market" value="Asheville"' in res.text
    assert 'name="first_item" value="Spring agnolotti"' in res.text


def test_signup_normalizes_plan_to_selected_audience(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client, audience="photographer", plan="restaurant_starter",
                 company="Avery Photo Co.", email="avery-photo@example.com")
    assert res.status_code == 303
    org = db.one("SELECT * FROM organizations WHERE slug='avery-photo-co'")
    assert org["audience"] == "photographer"
    assert org["plan"] == "photographer_studio"
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    assert sub["plan"] == "photographer_studio"


def test_signup_workspace_generate_pack(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)

    client = TestClient(app)
    res = signup(client)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/w/blue-plate?job=")
    assert res.headers["location"].endswith("#jobs")

    assert client.get("/w/blue-plate").status_code == 200
    assert db.one("SELECT role FROM organization_members")["role"] == "owner"
    assert db.one("SELECT status FROM subscriptions")["status"] == "trialing"

    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert org["market"] == "Asheville"
    assert org["brand_voice"] == "warm and chef-led"
    item = db.one("SELECT * FROM menu_items WHERE org_id=?", (org["id"],))
    assert item["name"] == "Spring agnolotti"
    campaign = db.one("SELECT * FROM campaigns LIMIT 1")
    assert campaign["goal"] == "fill weekday reservations"
    pack = db.one("SELECT * FROM content_packs")
    assert pack and "Spring agnolotti" in pack["body_json"]
    assert "fill weekday reservations" in pack["body_json"]


def test_signup_queues_initial_pack_until_worker_drains(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)

    client = TestClient(app)
    res = signup(client, drain=False)
    assert res.status_code == 303
    job = db.one("SELECT * FROM jobs WHERE kind='generate_pack'")
    assert res.headers["location"] == f"/w/blue-plate?job={job['id']}#jobs"
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 0

    page = client.get(res.headers["location"])
    assert page.status_code == 200
    assert "Generation jobs" in page.text
    assert "queued" in page.text

    assert jobs.drain(limit=1) == 1
    done = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    pack = db.one("SELECT * FROM content_packs")
    assert done["status"] == "done"
    assert done["attempts"] == 1
    assert done["result_pack_id"] == pack["id"]
    assert "Spring agnolotti" in pack["body_json"]


def test_auth_cookies_use_secure_flag_when_configured(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import config
    config.COOKIE_SECURE = True
    client = TestClient(app)

    res = signup(client)
    cookies = res.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Secure" in cookie for cookie in cookies)
    assert all("HttpOnly" in cookie for cookie in cookies)

    login = client.post("/login", data={
        "email": "avery@example.com",
        "password": "correct-horse",
    }, follow_redirects=False)
    login_cookies = login.headers.get_list("set-cookie")
    assert len(login_cookies) == 2
    assert all("Secure" in cookie for cookie in login_cookies)


def test_login_restores_workspace_access(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    login = client.post("/login", data={
        "email": "avery@example.com",
        "password": "correct-horse",
    }, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/w/blue-plate"
    assert client.get("/w/blue-plate").status_code == 200


def test_workspace_redirects_anonymous_to_login_next(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    anon = TestClient(app)
    res = anon.get("/w/blue-plate", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login?next=%2Fw%2Fblue-plate"


def test_workspace_cookie_without_user_cookie_is_not_authorization(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    workspace_cookie = security.workspace_cookie("blue-plate")
    cookie_header = f"{security.WORKSPACE_COOKIE}={workspace_cookie}"

    stale = TestClient(app)
    page = stale.get(
        "/w/blue-plate",
        headers={"Cookie": cookie_header},
        follow_redirects=False,
    )
    mutation = stale.post(
        "/w/blue-plate/menu",
        data={"name": "Unauthorized dish"},
        headers={"Cookie": cookie_header},
        follow_redirects=False,
    )

    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=%2Fw%2Fblue-plate"
    assert mutation.status_code == 403
    assert db.one("SELECT * FROM menu_items WHERE name='Unauthorized dish'") is None


def test_login_honors_safe_workspace_next(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    login = client.post("/login", data={
        "email": "avery@example.com",
        "password": "correct-horse",
        "next": "/w/blue-plate/billing",
    }, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/w/blue-plate/billing"
    assert client.get("/w/blue-plate/billing").status_code == 200


def test_login_rejects_external_next(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    login = client.post("/login", data={
        "email": "avery@example.com",
        "password": "correct-horse",
        "next": "https://evil.example/w/blue-plate",
    }, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/w/blue-plate"


def test_duplicate_signup_renders_friendly_login_prompt(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    duplicate = signup(client)
    assert duplicate.status_code == 400
    assert "An account already exists for this email" in duplicate.text
    assert "Log in instead" in duplicate.text
    assert 'name="email" type="email" value="avery@example.com"' in duplicate.text


def test_plan_gate_blocks_locked_recipe_for_starter(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client, plan="restaurant_starter")
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='press-seasonal'")
    gen = client.post(f"/w/blue-plate/campaigns/{campaign['id']}/generate",
                      data={"recipe_id": recipe["id"]},
                      follow_redirects=False)
    assert gen.status_code == 402


def test_settings_requires_logged_in_owner(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    anon = TestClient(app)
    res = anon.get("/w/blue-plate/settings", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login?next=%2Fw%2Fblue-plate%2Fsettings"


def test_settings_update_persists_workspace_basics(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    update = client.post("/w/blue-plate/settings", data={
        "company": "Blue Plate Cafe",
        "email": "ops@blueplate.example",
        "market": "Charlotte",
        "service_mix": "dine-in, catering",
        "brand_voice": "polished and local",
    }, follow_redirects=False)
    assert update.status_code == 303
    assert update.headers["location"] == "/w/blue-plate/settings?notice=saved"
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert org["company"] == "Blue Plate Cafe"
    assert org["email"] == "ops@blueplate.example"
    assert org["market"] == "Charlotte"
    assert org["service_mix"] == "dine-in, catering"
    assert org["brand_voice"] == "polished and local"
    event = db.one("SELECT * FROM audit_events WHERE action='workspace.settings_updated'")
    assert event
    assert event["actor_user_id"]
    assert "contact email" in event["summary"]
    assert "brand voice" in event["summary"]
    assert json.loads(event["details_json"])["fields"]
    settings = client.get("/w/blue-plate/settings")
    assert "Activity trail" in settings.text
    assert "Updated workspace basics" in settings.text
    assert "Avery (avery@example.com)" in settings.text


def test_settings_rotate_workspace_access_token(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    old = db.one("SELECT access_token FROM organizations WHERE slug='blue-plate'")["access_token"]
    rotated = client.post("/w/blue-plate/settings/token", follow_redirects=False)
    assert rotated.status_code == 303
    assert rotated.headers["location"] == "/w/blue-plate/settings?rotated=1"
    new = db.one("SELECT access_token FROM organizations WHERE slug='blue-plate'")["access_token"]
    assert new != old
    event = db.one("SELECT * FROM audit_events WHERE action='workspace.token_rotated'")
    assert event and new[-6:] in event["summary"]
    assert json.loads(event["details_json"])["token_tail"] == new[-6:]


def test_settings_invites_new_member_and_acceptance_creates_access(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    invite = client.post("/w/blue-plate/settings/members/invite", data={
        "invitee_name": "Jordan",
        "email": "jordan@example.com",
        "role": "member",
    }, follow_redirects=False)
    assert invite.status_code == 303
    pending = db.one("SELECT * FROM workspace_invites WHERE email='jordan@example.com'")
    assert pending and pending["status"] == "pending"
    assert pending["role"] == "member"
    assert invite.headers["location"] == f"/w/blue-plate/settings?invited={pending['id']}"
    settings = client.get(invite.headers["location"])
    assert settings.status_code == 200
    assert "Invite ready." in settings.text
    assert f"http://localhost:8450/invite/{pending['token']}" in settings.text
    assert "jordan@example.com" in settings.text
    event = db.one("SELECT * FROM audit_events WHERE action='member.invited'")
    assert event and "jordan@example.com" in event["summary"]

    invitee = TestClient(app)
    form = invitee.get(f"/invite/{pending['token']}")
    assert form.status_code == 200
    assert "Join Blue Plate" in form.text
    accepted = invitee.post(f"/invite/{pending['token']}/accept", data={
        "name": "Jordan",
        "password": "correct-horse",
    }, follow_redirects=False)
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/w/blue-plate"
    user = db.one("SELECT * FROM users WHERE email='jordan@example.com'")
    member = db.one("SELECT * FROM organization_members WHERE user_id=?", (user["id"],))
    assert member["role"] == "member"
    accepted_invite = db.one("SELECT * FROM workspace_invites WHERE id=?", (pending["id"],))
    assert accepted_invite["status"] == "accepted"
    assert invitee.get("/w/blue-plate").status_code == 200
    assert invitee.get("/w/blue-plate/settings").status_code == 403
    accepted_event = db.one("SELECT * FROM audit_events WHERE action='member.invite_accepted'")
    assert accepted_event and accepted_event["actor_user_id"] == user["id"]


def test_plain_members_cannot_access_billing_money_path(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    owner = TestClient(app)
    signup(owner)
    owner.post("/w/blue-plate/settings/members/invite", data={
        "email": "casey@example.com",
        "role": "member",
    }, follow_redirects=False)
    invite = db.one("SELECT * FROM workspace_invites WHERE email='casey@example.com'")
    member_client = TestClient(app)
    member_client.post(f"/invite/{invite['token']}/accept", data={
        "name": "Casey",
        "password": "correct-horse",
    }, follow_redirects=False)

    page = member_client.get("/w/blue-plate")
    billing = member_client.get("/w/blue-plate/billing", follow_redirects=False)
    plan = member_client.post(
        "/w/blue-plate/billing/plan",
        data={"plan": "restaurant_starter"},
        follow_redirects=False,
    )
    checkout = member_client.post("/w/blue-plate/billing/checkout", follow_redirects=False)

    assert page.status_code == 200
    assert 'href="/w/blue-plate/billing"' not in page.text
    assert "Ask an owner or admin to manage billing." in page.text
    assert billing.status_code == 403
    assert plan.status_code == 403
    assert checkout.status_code == 403
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert org["plan"] == "restaurant_growth"


def test_plain_members_can_draft_but_not_publish_packs(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    owner = TestClient(app)
    signup(owner)
    owner.post("/w/blue-plate/settings/members/invite", data={
        "email": "publisher@example.com",
        "role": "member",
    }, follow_redirects=False)
    invite = db.one("SELECT * FROM workspace_invites WHERE email='publisher@example.com'")
    member_client = TestClient(app)
    member_client.post(f"/invite/{invite['token']}/accept", data={
        "name": "Publisher",
        "password": "correct-horse",
    }, follow_redirects=False)
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='menu-launch'")

    generated = member_client.post(
        f"/w/blue-plate/campaigns/{campaign['id']}/generate",
        data={"recipe_id": recipe["id"]},
        follow_redirects=False,
    )
    jobs.drain()
    pack = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    page = member_client.get("/w/blue-plate")
    approve = member_client.post(
        f"/w/blue-plate/packs/{pack['id']}/approve",
        follow_redirects=False,
    )
    share = member_client.post(
        f"/w/blue-plate/packs/{pack['id']}/share",
        follow_redirects=False,
    )
    export = member_client.get(
        f"/w/blue-plate/packs/{pack['id']}/export.md",
        follow_redirects=False,
    )
    revise = member_client.post(
        f"/w/blue-plate/packs/{pack['id']}/revise",
        data={
            "title": "Member rewrite",
            "strategy": "Member strategy",
            "shot_list": "Member shot",
        },
        follow_redirects=False,
    )
    regenerate = member_client.post(
        f"/w/blue-plate/packs/{pack['id']}/regenerate",
        data={"feedback": "Make it shorter."},
        follow_redirects=False,
    )
    unchanged = db.one("SELECT * FROM content_packs WHERE id=?", (pack["id"],))

    assert generated.status_code == 303
    assert pack["status"] == "draft"
    assert page.status_code == 200
    assert f'/w/blue-plate/packs/{pack["id"]}/approve' not in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/share' not in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/export.md' not in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/revise' not in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/regenerate' not in page.text
    assert "Ask an owner or admin to approve, share, or export this pack." in page.text
    assert approve.status_code == 403
    assert share.status_code == 403
    assert export.status_code == 403
    assert revise.status_code == 403
    assert regenerate.status_code == 403
    assert unchanged["status"] == "draft"
    assert unchanged["share_token"] is None
    assert unchanged["exported_at"] is None


def test_owner_can_revise_draft_pack_before_approval(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")
    page = client.get("/w/blue-plate")
    assert f'/w/blue-plate/packs/{pack["id"]}/revise' in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/share' not in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/export.md' not in page.text

    draft_share = client.post(f"/w/blue-plate/packs/{pack['id']}/share", follow_redirects=False)
    draft_export = client.get(f"/w/blue-plate/packs/{pack['id']}/export.md")
    unchanged = db.one("SELECT * FROM content_packs WHERE id=?", (pack["id"],))
    assert draft_share.status_code == 400
    assert draft_export.status_code == 400
    assert unchanged["share_token"] is None
    assert unchanged["exported_at"] is None

    revise = client.post(
        f"/w/blue-plate/packs/{pack['id']}/revise",
        data={
            "title": "Chef tasting rollout",
            "strategy": "Lead with the tasting menu and reserve Friday.",
            "shot_list": "- Hero scallop plate\nHands plating sauce",
            "captions": "Friday tables are built around this tasting menu.",
            "exports": "Instagram: reservation CTA",
            "upsells": "Add delivery-app crop set",
        },
        follow_redirects=False,
    )
    assert revise.status_code == 303
    assert revise.headers["location"] == f"/w/blue-plate?revised={pack['id']}#pack-{pack['id']}"

    updated = db.one("SELECT * FROM content_packs WHERE id=?", (pack["id"],))
    body = json.loads(updated["body_json"])
    assert updated["title"] == "Chef tasting rollout"
    assert updated["status"] == "draft"
    assert body["headline"] == "Chef tasting rollout"
    assert body["strategy"] == "Lead with the tasting menu and reserve Friday."
    assert body["shot_list"] == ["Hero scallop plate", "Hands plating sauce"]
    assert body["captions"] == ["Friday tables are built around this tasting menu."]
    assert body["exports"] == ["Instagram: reservation CTA"]
    assert body["upsells"] == ["Add delivery-app crop set"]
    assert json.loads(updated["ai_draft_original"])["headline"] == "First monthly content pack"

    event = db.one("SELECT * FROM audit_events WHERE action='pack.revised'")
    assert event["entity_id"] == pack["id"]
    assert "Chef tasting rollout" in event["summary"]

    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    assert approve.status_code == 303
    blocked = client.post(
        f"/w/blue-plate/packs/{pack['id']}/revise",
        data={
            "title": "After approval",
            "strategy": "Nope",
            "shot_list": "Nope",
        },
        follow_redirects=False,
    )
    assert blocked.status_code == 400


def test_owner_can_regenerate_feedback_draft_without_mutating_source(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")
    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    assert approve.status_code == 303
    share = client.post(f"/w/blue-plate/packs/{pack['id']}/share", follow_redirects=False)
    assert share.status_code == 303
    source = db.one("SELECT * FROM content_packs WHERE id=?", (pack["id"],))
    page = client.get("/w/blue-plate")
    assert f'/w/blue-plate/packs/{pack["id"]}/regenerate' in page.text

    regenerated = client.post(
        f"/w/blue-plate/packs/{pack['id']}/regenerate",
        data={
            "feedback": (
                "Make this shorter, more premium, and focused on DoorDash delivery."
            )
        },
        follow_redirects=False,
    )
    job = db.one("SELECT * FROM jobs WHERE kind='regenerate_pack' ORDER BY id DESC LIMIT 1")
    assert regenerated.status_code == 303
    assert regenerated.headers["location"] == f"/w/blue-plate?job={job['id']}#jobs"
    assert job["status"] == "queued"
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1

    assert jobs.drain(limit=1) == 1
    done = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    new_pack = db.one("SELECT * FROM content_packs WHERE id=?", (done["result_pack_id"],))

    source_after = db.one("SELECT * FROM content_packs WHERE id=?", (pack["id"],))
    body = json.loads(new_pack["body_json"])
    assert new_pack["status"] == "draft"
    assert new_pack["share_token"] is None
    assert new_pack["source_pack_id"] == pack["id"]
    assert new_pack["revision_note"].startswith("Make this shorter")
    assert new_pack["archived_at"] is None
    assert new_pack["title"] == "First monthly content pack feedback draft"
    assert source_after["status"] == "approved"
    assert source_after["share_token"] == source["share_token"]
    assert body["provenance"]["engine"] == "dionysus-feedback-regenerate"
    assert body["provenance"]["source_pack_id"] == pack["id"]
    assert body["provenance"]["feedback"].startswith("Make this shorter")
    assert body["exports"][0].startswith("delivery_apps:")
    assert any("Elevated angle:" in caption for caption in body["captions"])
    assert all(len(caption) <= 118 for caption in body["captions"])

    event = db.one("SELECT * FROM audit_events WHERE action='pack.regenerated'")
    assert event["entity_id"] == new_pack["id"]
    assert "new draft" in event["summary"]

    refreshed = client.get("/w/blue-plate")
    assert "Regenerated from First monthly content pack" in refreshed.text
    assert "Feedback: Make this shorter" in refreshed.text


def test_archived_drafts_are_hidden_from_workspace_and_mise_by_default(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")

    archive = client.post(f"/w/blue-plate/packs/{pack['id']}/archive", follow_redirects=False)
    assert archive.status_code == 303
    assert archive.headers["location"] == f"/w/blue-plate?archived=1#pack-{pack['id']}"
    archived = db.one("SELECT * FROM content_packs WHERE id=?", (pack["id"],))
    assert archived["archived_at"]

    page = client.get("/w/blue-plate")
    assert page.status_code == 200
    assert f'id="pack-{pack["id"]}"' not in page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/regenerate' not in page.text
    assert "Show archived (1)" in page.text

    archived_page = client.get("/w/blue-plate?archived=1")
    assert archived_page.status_code == 200
    assert "First monthly content pack" in archived_page.text
    assert "archived" in archived_page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/approve' not in archived_page.text
    assert f'/w/blue-plate/packs/{pack["id"]}/regenerate' not in archived_page.text

    mise = client.get(
        "/api/mise/organizations/blue-plate/packs?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert mise.status_code == 200
    assert mise.json()["packs"] == []

    revise = client.post(
        f"/w/blue-plate/packs/{pack['id']}/revise",
        data={
            "title": "Archived rewrite",
            "strategy": "Nope",
            "shot_list": "Nope",
        },
        follow_redirects=False,
    )
    regenerate = client.post(
        f"/w/blue-plate/packs/{pack['id']}/regenerate",
        data={"feedback": "Make this premium."},
        follow_redirects=False,
    )
    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    assert revise.status_code == 400
    assert regenerate.status_code == 400
    assert approve.status_code == 400

    event = db.one("SELECT * FROM audit_events WHERE action='pack.archived'")
    assert event["entity_id"] == pack["id"]


def test_approved_packs_cannot_be_archived(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")
    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    archive = client.post(f"/w/blue-plate/packs/{pack['id']}/archive", follow_redirects=False)
    assert approve.status_code == 303
    assert archive.status_code == 400
    assert db.one("SELECT archived_at FROM content_packs WHERE id=?",
                  (pack["id"],))["archived_at"] is None


def test_failed_regeneration_job_can_retry_without_duplicate_pack(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")
    original_regenerate = generator.regenerate_with_feedback

    def failing_regenerate(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(generator, "regenerate_with_feedback", failing_regenerate)
    failed = client.post(
        f"/w/blue-plate/packs/{pack['id']}/regenerate",
        data={"feedback": "Make this more premium."},
        follow_redirects=False,
    )
    job = db.one("SELECT * FROM jobs WHERE kind='regenerate_pack' ORDER BY id DESC LIMIT 1")
    assert failed.status_code == 303
    assert failed.headers["location"] == f"/w/blue-plate?job={job['id']}#jobs"
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1

    assert jobs.drain(limit=1) == 1
    job = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert job["status"] == "failed"
    assert job["attempts"] == 1
    assert "model unavailable" in job["error"]
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1

    page = client.get(f"/w/blue-plate?job={job['id']}#jobs")
    assert "Generation jobs" in page.text
    assert "model unavailable" in page.text
    assert f'/w/blue-plate/jobs/{job["id"]}/retry' in page.text

    support = client.get("/w/blue-plate/support")
    assert support.status_code == 200
    assert "Generation jobs" in support.text
    assert "1 failed jobs" in support.text

    retry_failed = client.post(f"/w/blue-plate/jobs/{job['id']}/retry", follow_redirects=False)
    requeued = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert retry_failed.status_code == 303
    assert retry_failed.headers["location"] == f"/w/blue-plate?job={job['id']}#jobs"
    assert requeued["status"] == "queued"
    assert requeued["attempts"] == 1
    assert jobs.drain(limit=1) == 1
    still_failed = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert still_failed["status"] == "failed"
    assert still_failed["attempts"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1

    monkeypatch.setattr(generator, "regenerate_with_feedback", original_regenerate)
    retry_success = client.post(f"/w/blue-plate/jobs/{job['id']}/retry", follow_redirects=False)
    queued = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert retry_success.status_code == 303
    assert retry_success.headers["location"] == f"/w/blue-plate?job={job['id']}#jobs"
    assert queued["status"] == "queued"
    assert jobs.drain(limit=1) == 1
    done = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert done["status"] == "done"
    assert done["attempts"] == 3
    assert done["result_pack_id"]
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 2
    new_pack = db.one("SELECT * FROM content_packs WHERE id=?", (done["result_pack_id"],))
    assert new_pack["source_pack_id"] == pack["id"]
    assert new_pack["revision_note"] == "Make this more premium."
    event = db.one("SELECT * FROM audit_events WHERE action='job.retried'")
    assert event["entity_id"] == job["id"]


def test_feedback_regeneration_respects_monthly_pack_limit(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client, plan="restaurant_starter")
    pack = db.one("SELECT * FROM content_packs")
    for note in ("Make it premium.", "Make it social."):
        res = client.post(
            f"/w/blue-plate/packs/{pack['id']}/regenerate",
            data={"feedback": note},
            follow_redirects=False,
        )
        assert res.status_code == 303

    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert jobs.pending_pack_count(org["id"]) == 2
    blocked = client.post(
        f"/w/blue-plate/packs/{pack['id']}/regenerate",
        data={"feedback": "Make it delivery focused."},
        follow_redirects=False,
    )
    assert blocked.status_code == 402
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1
    assert jobs.drain(limit=2) == 2
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 3


def test_member_role_update_and_revoke_remove_access(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    owner = TestClient(app)
    res = signup(owner)
    invite = owner.post("/w/blue-plate/settings/members/invite", data={
        "email": "casey@example.com",
        "role": "member",
    }, follow_redirects=False)
    pending = db.one("SELECT * FROM workspace_invites WHERE email='casey@example.com'")
    member_client = TestClient(app)
    accepted = member_client.post(f"/invite/{pending['token']}/accept", data={
        "name": "Casey",
        "password": "correct-horse",
    }, follow_redirects=False)
    user = db.one("SELECT * FROM users WHERE email='casey@example.com'")

    role = owner.post(f"/w/blue-plate/settings/members/{user['id']}/role",
                      data={"role": "admin"},
                      follow_redirects=False)
    assert role.status_code == 303
    assert db.one("SELECT role FROM organization_members WHERE user_id=?",
                  (user["id"],))["role"] == "admin"
    assert member_client.get("/w/blue-plate/settings").status_code == 200

    revoke = owner.post(f"/w/blue-plate/settings/members/{user['id']}/revoke", follow_redirects=False)
    assert revoke.status_code == 303
    assert db.one("SELECT * FROM organization_members WHERE user_id=?",
                  (user["id"],)) is None
    assert member_client.get("/w/blue-plate").status_code == 403
    actions = [row["action"] for row in db.all_(
        "SELECT action FROM audit_events ORDER BY id")]
    assert "member.role_updated" in actions
    assert "member.revoked" in actions


def test_pending_invite_can_be_revoked(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    client.post("/w/blue-plate/settings/members/invite", data={
        "email": "taylor@example.com",
        "role": "admin",
    }, follow_redirects=False)
    invite = db.one("SELECT * FROM workspace_invites WHERE email='taylor@example.com'")
    revoked = client.post(f"/w/blue-plate/settings/invites/{invite['id']}/revoke", follow_redirects=False)
    assert revoked.status_code == 303
    assert db.one("SELECT status FROM workspace_invites WHERE id=?",
                  (invite["id"],))["status"] == "revoked"
    assert client.get(f"/invite/{invite['token']}").status_code == 404
    event = db.one("SELECT * FROM audit_events WHERE action='member.invite_revoked'")
    assert event and "taylor@example.com" in event["summary"]


def test_settings_revoke_share_token_removes_public_access(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    pack = db.one("SELECT * FROM content_packs")
    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    assert approve.status_code == 303
    share = client.post(f"/w/blue-plate/packs/{pack['id']}/share", follow_redirects=False)
    assert share.status_code == 303
    token = db.one("SELECT share_token FROM content_packs WHERE id=?", (pack["id"],))["share_token"]
    assert client.get(f"/share/{token}").status_code == 200

    revoke = client.post(f"/w/blue-plate/settings/packs/{pack['id']}/revoke-share", follow_redirects=False)
    assert revoke.status_code == 303
    assert revoke.headers["location"] == f"/w/blue-plate/settings?revoked={pack['id']}"
    assert db.one("SELECT share_token FROM content_packs WHERE id=?", (pack["id"],))["share_token"] is None
    assert client.get(f"/share/{token}").status_code == 404
    actions = [row["action"] for row in db.all_(
        "SELECT action FROM audit_events ORDER BY id")]
    assert "pack.share_enabled" in actions
    assert "pack.share_revoked" in actions


def test_pack_approve_and_workspace_export_write_audit_events(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    pack = db.one("SELECT * FROM content_packs")
    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    assert approve.status_code == 303
    export = client.get(f"/w/blue-plate/packs/{pack['id']}/export.md")
    assert export.status_code == 200
    actions = [row["action"] for row in db.all_(
        "SELECT action FROM audit_events ORDER BY id")]
    assert "pack.approved" in actions
    assert "pack.exported" in actions


def test_audit_filters_activity_by_action(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    client.post("/w/blue-plate/settings", data={
        "company": "Blue Plate Cafe",
        "email": "ops@blueplate.example",
        "market": "Charlotte",
        "service_mix": "dine-in, catering",
        "brand_voice": "polished and local",
    }, follow_redirects=False)
    client.post("/w/blue-plate/settings/token", follow_redirects=False)

    page = client.get(
        "/w/blue-plate/settings?audit_action=workspace.token_rotated#activity")
    assert page.status_code == 200
    assert "Rotated workspace token" in page.text
    assert "Updated workspace basics" not in page.text
    assert 'value="workspace.token_rotated" selected' in page.text
    assert "/w/blue-plate/settings/audit/export.csv?audit_action=workspace.token_rotated" in page.text


def test_audit_filters_activity_by_actor_and_date(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    owner = db.one("SELECT * FROM users WHERE email='avery@example.com'")
    db.run("""INSERT INTO audit_events
              (org_id, actor_user_id, action, summary, details_json, created_at)
              VALUES (?,?,?,?,?,?)""",
           (org["id"], None, "system.test", "System test event", "{}", "2026-01-02 12:00:00"))
    db.run("""INSERT INTO audit_events
              (org_id, actor_user_id, action, summary, details_json, created_at)
              VALUES (?,?,?,?,?,?)""",
           (org["id"], owner["id"], "user.test", "User test event", "{}", "2026-01-02 12:00:00"))

    page = client.get(
        "/w/blue-plate/settings?audit_actor=system&audit_from=2026-01-02&audit_to=2026-01-02")
    assert page.status_code == 200
    assert "System test event" in page.text
    assert "User test event" not in page.text
    assert 'value="system" selected' in page.text
    assert 'name="audit_from" type="date" value="2026-01-02"' in page.text


def test_audit_export_json_and_csv_respect_filters(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    client.post("/w/blue-plate/settings/token", follow_redirects=False)
    client.post("/w/blue-plate/settings", data={
        "company": "Blue Plate Cafe",
        "email": "ops@blueplate.example",
        "market": "Charlotte",
        "service_mix": "dine-in, catering",
        "brand_voice": "polished and local",
    }, follow_redirects=False)

    json_export = client.get(
        "/w/blue-plate/settings/audit/export.json?audit_action=workspace.token_rotated")
    assert json_export.status_code == 200
    assert json_export.headers["content-type"].startswith("application/json")
    assert "blue-plate-audit.json" in json_export.headers["content-disposition"]
    body = json_export.json()
    assert len(body) == 1
    assert body[0]["action"] == "workspace.token_rotated"
    assert body[0]["details"]["token_tail"]

    csv_export = client.get(
        "/w/blue-plate/settings/audit/export.csv?audit_action=workspace.token_rotated")
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    assert "created_at,actor,action,entity_type,entity_id,summary,details_json" in csv_export.text
    assert "workspace.token_rotated" in csv_export.text
    assert "workspace.settings_updated" not in csv_export.text


def test_support_dashboard_surfaces_operator_state(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    client.post("/w/blue-plate/settings/members/invite", data={
        "email": "jordan@example.com",
        "role": "admin",
    }, follow_redirects=False)
    pack = db.one("SELECT * FROM content_packs")
    client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    client.post(f"/w/blue-plate/packs/{pack['id']}/share", follow_redirects=False)

    page = client.get("/w/blue-plate/support")
    assert page.status_code == 200
    assert "Support dashboard" in page.text
    assert "Workspace state" in page.text
    assert "Access roster" in page.text
    assert "Invite states" in page.text
    assert "Recent activity" in page.text
    assert "jordan@example.com" in page.text
    assert "pending" in page.text
    assert "owner · active" in page.text
    assert "pack.approved" in page.text
    assert "/w/blue-plate/settings/audit/events/" in page.text

    anon = TestClient(app)
    redirect = anon.get("/w/blue-plate/support", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/login?next=%2Fw%2Fblue-plate%2Fsupport"


def test_audit_detail_renders_event_details_for_owner_only(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    owner = TestClient(app)
    res = signup(owner)
    owner.post("/w/blue-plate/settings/token", follow_redirects=False)
    event = db.one("SELECT * FROM audit_events WHERE action='workspace.token_rotated'")

    page = owner.get(f"/w/blue-plate/settings/audit/events/{event['id']}")
    assert page.status_code == 200
    assert f"Audit event #{event['id']}" in page.text
    assert "workspace.token_rotated" in page.text
    assert "Rotated workspace token" in page.text
    assert "token_tail" in page.text
    assert "Avery (avery@example.com)" in page.text

    owner.post("/w/blue-plate/settings/members/invite", data={
        "email": "casey@example.com",
        "role": "member",
    }, follow_redirects=False)
    invite = db.one("SELECT * FROM workspace_invites WHERE email='casey@example.com'")
    member_client = TestClient(app)
    accepted = member_client.post(f"/invite/{invite['token']}/accept", data={
        "name": "Casey",
        "password": "correct-horse",
    }, follow_redirects=False)
    forbidden = member_client.get(
        f"/w/blue-plate/settings/audit/events/{event['id']}")
    assert forbidden.status_code == 403


def test_billing_page_shows_trial_when_stripe_unconfigured(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    billing = client.get("/w/blue-plate/billing")
    assert billing.status_code == 200
    assert "trialing" in billing.text
    assert "Stripe keys" in billing.text


def test_mise_api_is_dormant_or_bearer_gated(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.get("/api/mise/organizations/nope/latest-pack").status_code == 401
    assert client.get(
        "/api/mise/organizations/nope/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    ).status_code == 404



def test_configured_stripe_checkout_redirects_to_session_url(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import billing, config
    config.STRIPE_SECRET_KEY = "sk_test"
    config.STRIPE_PRICE_RESTAURANT_GROWTH = "price_growth"

    class FakeSession:
        @staticmethod
        def create(**kwargs):
            assert kwargs["mode"] == "subscription"
            assert kwargs["line_items"] == [{"price": "price_growth", "quantity": 1}]
            assert kwargs["metadata"]["plan"] == "restaurant_growth"
            return {"url": "https://checkout.stripe.test/session"}

    class FakeStripe:
        api_key = None
        checkout = type("checkout", (), {"Session": FakeSession})

    monkeypatch.setattr(billing, "_stripe", lambda: FakeStripe)
    client = TestClient(app)
    res = signup(client)
    checkout = client.post("/w/blue-plate/billing/checkout", follow_redirects=False)
    assert checkout.status_code == 303
    assert checkout.headers["location"] == "https://checkout.stripe.test/session"
    event = db.one("SELECT * FROM audit_events WHERE action='billing.checkout_started'")
    assert event and "Restaurant Growth" in event["summary"]


def test_stripe_webhook_marks_subscription_active(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import billing, config
    config.STRIPE_WEBHOOK_SECRET = "whsec_test"
    client = TestClient(app)
    signup(client)
    org = db.one("SELECT id FROM organizations WHERE slug='blue-plate'")

    async def fake_construct(request):
        return {
            "type": "checkout.session.completed",
            "data": {"object": {
                "client_reference_id": str(org["id"]),
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"org_id": str(org["id"]), "plan": "restaurant_growth"},
            }},
        }

    monkeypatch.setattr(billing, "construct_webhook_event", fake_construct)
    res = client.post("/stripe/webhook", content=b"{}",
                      headers={"stripe-signature": "sig"})
    assert res.status_code == 200
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    assert sub["status"] == "active"
    assert sub["stripe_customer_id"] == "cus_123"
    assert sub["stripe_subscription_id"] == "sub_123"
    event = db.one("SELECT * FROM audit_events WHERE action='billing.checkout_completed'")
    assert event
    assert event["actor_user_id"] is None
    assert "subscription marked active" in event["summary"]
    assert json.loads(event["details_json"])["status"] == "active"


def test_stripe_webhook_accepts_stripe_event_objects(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import billing, config
    config.STRIPE_WEBHOOK_SECRET = "whsec_test"
    client = TestClient(app)
    signup(client)
    org = db.one("SELECT id FROM organizations WHERE slug='blue-plate'")

    class FakeEvent:
        def to_dict(self):
            return {
                "type": "checkout.session.completed",
                "data": {"object": {
                    "client_reference_id": str(org["id"]),
                    "customer": "cus_obj",
                    "subscription": "sub_obj",
                    "metadata": {"org_id": str(org["id"]), "plan": "restaurant_growth"},
                }},
            }

    class FakeWebhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            assert secret == "whsec_test"
            assert sig == "sig"
            return FakeEvent()

    class FakeStripe:
        Webhook = FakeWebhook

    monkeypatch.setattr(billing, "_stripe", lambda: FakeStripe)
    res = client.post("/stripe/webhook", content=b"{}",
                      headers={"stripe-signature": "sig"})
    assert res.status_code == 200
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    assert sub["status"] == "active"
    assert sub["stripe_customer_id"] == "cus_obj"
    assert sub["stripe_subscription_id"] == "sub_obj"


def test_readiness_fails_with_default_dev_config(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import config
    config.SECRET_KEY = "dev-dionysus-secret"
    config.BASE_URL = "http://localhost:8450"
    config.COOKIE_SECURE = False
    config.STRIPE_SECRET_KEY = ""
    config.STRIPE_WEBHOOK_SECRET = ""
    config.STRIPE_PRICE_RESTAURANT_STARTER = ""
    config.STRIPE_PRICE_RESTAURANT_GROWTH = ""
    config.STRIPE_PRICE_PHOTOGRAPHER_STUDIO = ""
    config.MISE_IMPORT_TOKEN = ""
    client = TestClient(app)
    res = client.get("/readiness")
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is False
    assert any(c["key"] == "secret_key" and not c["ok"] for c in body["checks"])


def test_readiness_passes_when_production_env_is_armed(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import config
    config.SECRET_KEY = "a-real-secret-value"
    config.BASE_URL = "https://platekit.example.com"
    config.COOKIE_SECURE = True
    config.STRIPE_SECRET_KEY = "sk_test_123"
    config.STRIPE_WEBHOOK_SECRET = "whsec_123"
    config.STRIPE_PRICE_RESTAURANT_STARTER = "price_123"
    config.STRIPE_PRICE_RESTAURANT_GROWTH = "price_456"
    config.STRIPE_PRICE_PHOTOGRAPHER_STUDIO = "price_789"
    config.MISE_IMPORT_TOKEN = "mise-token"
    client = TestClient(app)
    res = client.get("/readiness")
    assert res.status_code == 200
    assert res.json()["ready"] is True



def test_pack_share_page_and_exports(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    pack = db.one("SELECT * FROM content_packs")

    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    assert approve.status_code == 303
    assert db.one("SELECT status FROM content_packs WHERE id=?", (pack["id"],))["status"] == "approved"

    share = client.post(f"/w/blue-plate/packs/{pack['id']}/share", follow_redirects=False)
    assert share.status_code == 303
    assert share.headers["location"] == f"/w/blue-plate?shared={pack['id']}#pack-{pack['id']}"
    token = db.one("SELECT share_token FROM content_packs WHERE id=?", (pack["id"],))["share_token"]
    assert token
    workspace = client.get(share.headers["location"])
    assert workspace.status_code == 200
    assert "Share link ready" in workspace.text
    assert f"http://localhost:8450/share/{token}" in workspace.text

    public = client.get(f"/share/{token}")
    assert public.status_code == 200
    assert "Strategy Brief" in public.text
    assert "Spring agnolotti" in public.text

    md = client.get(f"/share/{token}/export.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "# First monthly content pack" in md.text
    assert "## Shot List" in md.text
    assert db.one("SELECT status FROM content_packs WHERE id=?", (pack["id"],))["status"] == "approved"

    owner_export = client.get(f"/w/blue-plate/packs/{pack['id']}/export.md")
    assert owner_export.status_code == 200
    assert db.one("SELECT status FROM content_packs WHERE id=?", (pack["id"],))["status"] == "exported"

    txt = client.get(f"/share/{token}/copy.txt")
    assert txt.status_code == 200
    assert "Generated by Platekit" in txt.text


def test_workspace_export_requires_membership(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")
    anon = TestClient(app)
    res = anon.get(f"/w/blue-plate/packs/{pack['id']}/export.md",
                   follow_redirects=False)
    assert res.status_code == 403



def test_latest_pack_api_hides_newer_drafts_by_default(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    first = db.one("SELECT * FROM content_packs")

    hidden = client.get(
        "/api/mise/organizations/blue-plate/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["pack"] is None

    client.post(f"/w/blue-plate/packs/{first['id']}/approve", follow_redirects=False)
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='menu-launch'")
    client.post(f"/w/blue-plate/campaigns/{campaign['id']}/generate",
                data={"recipe_id": recipe["id"]},
                follow_redirects=False)
    jobs.drain()
    newest_draft = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    assert newest_draft["id"] != first["id"]
    assert newest_draft["status"] == "draft"

    latest = client.get(
        "/api/mise/organizations/blue-plate/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert latest.status_code == 200
    assert latest.json()["pack"]["id"] == first["id"]
    assert latest.json()["pack"]["status"] == "approved"

    drafts = client.get(
        "/api/mise/organizations/blue-plate/latest-pack?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert drafts.status_code == 200
    assert drafts.json()["pack"]["id"] == newest_draft["id"]
    assert drafts.json()["pack"]["status"] == "draft"


def test_mise_packs_api_returns_only_approved_or_exported_by_default(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    pack = db.one("SELECT * FROM content_packs")

    hidden = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["packs"] == []

    client.post(f"/w/blue-plate/packs/{pack['id']}/approve", follow_redirects=False)
    shown = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    body = shown.json()
    assert body["matched"] is True
    assert len(body["packs"]) == 1
    api_pack = body["packs"][0]
    assert api_pack["title"] == "First monthly content pack"
    assert api_pack["status"] == "approved"
    assert api_pack["campaign"]["title"] == "First monthly content pack"
    assert api_pack["share_url"] is None
    share_token = db.one(
        "SELECT share_token FROM content_packs WHERE id=?", (pack["id"],)
    )["share_token"]
    assert share_token is None
    assert "## Shot List" in api_pack["markdown"]
    assert "Spring agnolotti" in api_pack["markdown"]

    client.post(f"/w/blue-plate/packs/{pack['id']}/share", follow_redirects=False)
    shared = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    shared_pack = shared.json()["packs"][0]
    assert shared_pack["share_url"].startswith("http")


def test_mise_packs_api_can_include_drafts(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = db.one("SELECT * FROM content_packs")
    res = client.get(
        "/api/mise/organizations/blue-plate/packs?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert res.status_code == 200
    assert len(res.json()["packs"]) == 1
    assert res.json()["packs"][0]["share_url"] is None
    share_token = db.one(
        "SELECT share_token FROM content_packs WHERE id=?", (pack["id"],)
    )["share_token"]
    assert share_token is None



def test_cli_seed_demo_creates_approved_pack_for_mise_bridge(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    from app import cli, config
    config.BASE_URL = "https://platekit.example.com"
    assert cli.main(["seed-demo"]) == 0
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert org and org["company"] == "Blue Plate"
    pack = db.one("SELECT * FROM content_packs WHERE org_id=?", (org["id"],))
    assert pack["status"] == "approved"
    assert pack["share_token"]
    client = TestClient(app)
    res = client.get(
        "/api/mise/organizations/blue-plate/packs",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["packs"]) == 1
    assert body["packs"][0]["share_url"].startswith("https://platekit.example.com/share/")




def test_cli_worker_once_processes_queued_signup_job(tmp_path, monkeypatch, capsys):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client, drain=False)
    from app import cli

    assert cli.main(["worker", "--once"]) == 0
    output = capsys.readouterr().out
    assert "worker\tprocessed=1\tpending=0\tfailed=0" in output
    job = db.one("SELECT * FROM jobs WHERE kind='generate_pack'")
    assert job["status"] == "done"
    assert job["result_pack_id"]
    assert db.one("SELECT COUNT(*) AS n FROM content_packs")["n"] == 1


def test_cli_backup_creates_private_verified_snapshot(tmp_path, monkeypatch, capsys):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    from app import cli

    destination = tmp_path / "snapshots"
    assert cli.main(["backup", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "backup\t" in output
    assert "restore_check\tok\tintegrity=ok\tmigrations=7" in output

    snapshots = list(destination.glob("dionysus-*.db"))
    assert len(snapshots) == 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE(snapshots[0].stat().st_mode) == 0o600

    con = sqlite3.connect(snapshots[0])
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT slug FROM organizations").fetchone()[0] == "blue-plate"
        assert con.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 0
    finally:
        con.close()

    assert cli.main(["verify-backup", str(snapshots[0])]) == 0
    verify_output = capsys.readouterr().out
    assert "verify\tok\tintegrity=ok\tmigrations=7" in verify_output


def test_workspace_surfaces_upgrade_prompt_for_locked_recipe(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client, plan="restaurant_starter")
    page = client.get("/w/blue-plate")
    assert page.status_code == 200
    assert "Unlock Seasonal Press Kit with Restaurant Growth" in page.text


def test_billing_checkout_return_banners(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)

    success = client.get("/w/blue-plate/billing?checkout=success")
    assert success.status_code == 200
    assert "Checkout returned successfully." in success.text
    assert "subscription webhook arrives" in success.text

    cancel = client.get("/w/blue-plate/billing?checkout=cancel")
    assert cancel.status_code == 200
    assert "Checkout was canceled." in cancel.text
    assert "No billing changes were made" in cancel.text


def test_billing_can_switch_trial_plan(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client, plan="restaurant_starter")
    switch = client.post("/w/blue-plate/billing/plan",
                         data={"plan": "restaurant_growth"}, follow_redirects=False)
    assert switch.status_code == 303
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    assert org["plan"] == "restaurant_growth"
    assert sub["plan"] == "restaurant_growth"
