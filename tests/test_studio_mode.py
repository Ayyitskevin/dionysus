"""Studio mode — SaaS routes gated, Mise APIs stay live."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture(autouse=True)
def _studio(monkeypatch):
    monkeypatch.setattr(config, "STUDIO_MODE", True)
    monkeypatch.setattr(config, "MISE_IMPORT_TOKEN", "mise-test")


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