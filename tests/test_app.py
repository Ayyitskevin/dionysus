import os

from fastapi.testclient import TestClient

os.environ["DIONYSUS_DATA_DIR"] = "/tmp/dionysus-test-data"
os.environ["DIONYSUS_SECRET_KEY"] = "test-secret"
os.environ["DIONYSUS_MISE_IMPORT_TOKEN"] = "mise-test"

from app import db  # noqa: E402
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


def signup(client, **overrides):
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
    return client.post("/signup", data=data, follow_redirects=False)


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
    assert res.headers["location"] == "/w/blue-plate#packs"

    cookies = res.cookies
    assert client.get("/w/blue-plate", cookies=cookies).status_code == 200
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
    assert client.get("/w/blue-plate", cookies=login.cookies).status_code == 200


def test_workspace_redirects_anonymous_to_login_next(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    anon = TestClient(app)
    res = anon.get("/w/blue-plate", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login?next=%2Fw%2Fblue-plate"


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
    assert client.get("/w/blue-plate/billing", cookies=login.cookies).status_code == 200


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
    cookies = res.cookies
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='press-seasonal'")
    gen = client.post(f"/w/blue-plate/campaigns/{campaign['id']}/generate",
                      data={"recipe_id": recipe["id"]}, cookies=cookies,
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
    }, cookies=res.cookies, follow_redirects=False)
    assert update.status_code == 303
    assert update.headers["location"] == "/w/blue-plate/settings?notice=saved"
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    assert org["company"] == "Blue Plate Cafe"
    assert org["email"] == "ops@blueplate.example"
    assert org["market"] == "Charlotte"
    assert org["service_mix"] == "dine-in, catering"
    assert org["brand_voice"] == "polished and local"


def test_settings_rotate_workspace_access_token(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    old = db.one("SELECT access_token FROM organizations WHERE slug='blue-plate'")["access_token"]
    rotated = client.post("/w/blue-plate/settings/token",
                          cookies=res.cookies, follow_redirects=False)
    assert rotated.status_code == 303
    assert rotated.headers["location"] == "/w/blue-plate/settings?rotated=1"
    new = db.one("SELECT access_token FROM organizations WHERE slug='blue-plate'")["access_token"]
    assert new != old


def test_settings_revoke_share_token_removes_public_access(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    pack = db.one("SELECT * FROM content_packs")
    share = client.post(f"/w/blue-plate/packs/{pack['id']}/share",
                        cookies=res.cookies, follow_redirects=False)
    assert share.status_code == 303
    token = db.one("SELECT share_token FROM content_packs WHERE id=?", (pack["id"],))["share_token"]
    assert client.get(f"/share/{token}").status_code == 200

    revoke = client.post(f"/w/blue-plate/settings/packs/{pack['id']}/revoke-share",
                         cookies=res.cookies, follow_redirects=False)
    assert revoke.status_code == 303
    assert revoke.headers["location"] == f"/w/blue-plate/settings?revoked={pack['id']}"
    assert db.one("SELECT share_token FROM content_packs WHERE id=?", (pack["id"],))["share_token"] is None
    assert client.get(f"/share/{token}").status_code == 404


def test_billing_page_shows_trial_when_stripe_unconfigured(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)
    billing = client.get("/w/blue-plate/billing", cookies=res.cookies)
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
    checkout = client.post("/w/blue-plate/billing/checkout",
                           cookies=res.cookies, follow_redirects=False)
    assert checkout.status_code == 303
    assert checkout.headers["location"] == "https://checkout.stripe.test/session"


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
    cookies = res.cookies
    pack = db.one("SELECT * FROM content_packs")

    approve = client.post(f"/w/blue-plate/packs/{pack['id']}/approve",
                          cookies=cookies, follow_redirects=False)
    assert approve.status_code == 303
    assert db.one("SELECT status FROM content_packs WHERE id=?", (pack["id"],))["status"] == "approved"

    share = client.post(f"/w/blue-plate/packs/{pack['id']}/share",
                        cookies=cookies, follow_redirects=False)
    assert share.status_code == 303
    assert share.headers["location"] == f"/w/blue-plate?shared={pack['id']}#pack-{pack['id']}"
    token = db.one("SELECT share_token FROM content_packs WHERE id=?", (pack["id"],))["share_token"]
    assert token
    workspace = client.get(share.headers["location"], cookies=cookies)
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
    cookies = res.cookies
    first = db.one("SELECT * FROM content_packs")

    hidden = client.get(
        "/api/mise/organizations/blue-plate/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["pack"] is None

    client.post(f"/w/blue-plate/packs/{first['id']}/approve",
                cookies=cookies, follow_redirects=False)
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='menu-launch'")
    client.post(f"/w/blue-plate/campaigns/{campaign['id']}/generate",
                data={"recipe_id": recipe["id"]}, cookies=cookies,
                follow_redirects=False)
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

    client.post(f"/w/blue-plate/packs/{pack['id']}/approve",
                cookies=res.cookies, follow_redirects=False)
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
    assert api_pack["share_url"].startswith("http")
    assert "## Shot List" in api_pack["markdown"]
    assert "Spring agnolotti" in api_pack["markdown"]


def test_mise_packs_api_can_include_drafts(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    res = client.get(
        "/api/mise/organizations/blue-plate/packs?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert res.status_code == 200
    assert len(res.json()["packs"]) == 1



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



def test_workspace_surfaces_upgrade_prompt_for_locked_recipe(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client, plan="restaurant_starter")
    page = client.get("/w/blue-plate", cookies=res.cookies)
    assert page.status_code == 200
    assert "Unlock Seasonal Press Kit with Restaurant Growth" in page.text


def test_billing_checkout_return_banners(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client)

    success = client.get("/w/blue-plate/billing?checkout=success",
                         cookies=res.cookies)
    assert success.status_code == 200
    assert "Checkout returned successfully." in success.text
    assert "subscription webhook arrives" in success.text

    cancel = client.get("/w/blue-plate/billing?checkout=cancel",
                        cookies=res.cookies)
    assert cancel.status_code == 200
    assert "Checkout was canceled." in cancel.text
    assert "No billing changes were made" in cancel.text


def test_billing_can_switch_trial_plan(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = signup(client, plan="restaurant_starter")
    switch = client.post("/w/blue-plate/billing/plan",
                         data={"plan": "restaurant_growth"},
                         cookies=res.cookies, follow_redirects=False)
    assert switch.status_code == 303
    org = db.one("SELECT * FROM organizations WHERE slug='blue-plate'")
    sub = db.one("SELECT * FROM subscriptions WHERE org_id=?", (org["id"],))
    assert org["plan"] == "restaurant_growth"
    assert sub["plan"] == "restaurant_growth"
