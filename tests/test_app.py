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
    }
    data.update(overrides)
    return client.post("/signup", data=data, follow_redirects=False)


def test_signup_workspace_generate_pack(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)

    client = TestClient(app)
    res = signup(client)
    assert res.status_code == 303
    assert res.headers["location"] == "/w/blue-plate"

    cookies = res.cookies
    assert client.get("/w/blue-plate", cookies=cookies).status_code == 200
    assert db.one("SELECT role FROM organization_members")["role"] == "owner"
    assert db.one("SELECT status FROM subscriptions")["status"] == "trialing"

    client.post("/w/blue-plate/menu", data={
        "name": "Spring agnolotti",
        "category": "dish",
        "notes": "peas, ricotta, lemon",
    }, cookies=cookies)
    campaign = db.one("SELECT id FROM campaigns LIMIT 1")
    recipe = db.one("SELECT id FROM content_recipes WHERE slug='menu-launch'")
    gen = client.post(f"/w/blue-plate/campaigns/{campaign['id']}/generate",
                      data={"recipe_id": recipe["id"]}, cookies=cookies,
                      follow_redirects=False)
    assert gen.status_code == 303
    pack = db.one("SELECT * FROM content_packs")
    assert pack and "Spring agnolotti" in pack["body_json"]


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
