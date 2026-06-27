"""Studio mode — removed SaaS routes 404, Mise APIs stay live."""

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app


@pytest.fixture(autouse=True)
def _studio(tmp_path, monkeypatch):
    # Self-contained: /healthz and /readiness touch the DB, so set up a tmp one
    # rather than relying on another module having migrated first.
    monkeypatch.setenv("DIONYSUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dionysus.db")
    monkeypatch.setattr(config, "STUDIO_MODE", True)
    monkeypatch.setattr(config, "MISE_IMPORT_TOKEN", "mise-test")
    db.migrate()


@pytest.fixture()
def client():
    return TestClient(app)


def test_studio_home_is_operator_status(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Mise studio" in res.text


def test_studio_blocks_workspace_ui(client):
    assert client.get("/w/blue-plate").status_code == 404
    assert client.get("/pricing").status_code == 404
    assert client.get("/login").status_code == 404


def test_studio_health_reports_mode(client):
    body = client.get("/healthz").json()
    assert body["studio_mode"] is True
    assert body["studio"]["mise_bridge_armed"] is True


def test_studio_readiness_requires_mise_token_only(client):
    body = client.get("/readiness").json()
    assert body["studio_mode"] is True
    assert body["ready"] is True
    keys = {c["key"] for c in body["checks"]}
    assert "mise_bridge" in keys
    assert "stripe_secret" not in keys