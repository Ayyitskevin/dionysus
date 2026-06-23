import os

from fastapi.testclient import TestClient

os.environ["DIONYSUS_DATA_DIR"] = "/tmp/dionysus-test-data"
os.environ["DIONYSUS_SECRET_KEY"] = "test-secret"
os.environ["DIONYSUS_MISE_IMPORT_TOKEN"] = "mise-test"

from app import db  # noqa: E402
from app.main import app  # noqa: E402


def test_signup_workspace_generate_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONYSUS_DATA_DIR", str(tmp_path))
    from app import config
    config.DATA_DIR = tmp_path
    config.DB_PATH = tmp_path / "dionysus.db"
    db.migrate()

    client = TestClient(app)
    res = client.post("/signup", data={
        "name": "Avery",
        "email": "avery@example.com",
        "company": "Blue Plate",
        "audience": "restaurant",
        "plan": "growth",
    }, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/w/blue-plate"

    cookies = res.cookies
    assert client.get("/w/blue-plate", cookies=cookies).status_code == 200
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


def test_mise_api_is_dormant_or_bearer_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONYSUS_DATA_DIR", str(tmp_path))
    from app import config
    config.DATA_DIR = tmp_path
    config.DB_PATH = tmp_path / "dionysus.db"
    config.MISE_IMPORT_TOKEN = "mise-test"
    db.migrate()
    client = TestClient(app)
    assert client.get("/api/mise/organizations/nope/latest-pack").status_code == 401
    assert client.get(
        "/api/mise/organizations/nope/latest-pack",
        headers={"Authorization": "Bearer mise-test"},
    ).status_code == 404
