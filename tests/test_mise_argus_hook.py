"""Mise → Platekit Argus completion hook (mock Argus HTTP only)."""

import json

from fastapi.testclient import TestClient

from app import argus, db, jobs
from app.main import app
from tests.test_app import configure_tmp_db, signup


def test_argus_pack_hook_is_bearer_gated(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.post("/api/mise/organizations/blue-plate/argus-pack",
                       json={"argus_run_id": 1}).status_code == 401


def test_argus_pack_hook_creates_keyword_enriched_draft(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)

    def fake_context(run_id: int):
        return {
            "run_id": run_id,
            "photo_count": 2,
            "top_keywords": ["plating", "cocktail"],
            "top_shot_types": ["hero dish"],
            "hero_frames": ["hero dish — plating"],
        }

    monkeypatch.setattr(argus, "fetch_run_context", fake_context)

    res = client.post(
        "/api/mise/organizations/blue-plate/argus-pack",
        headers={"Authorization": "Bearer mise-test"},
        json={
            "argus_run_id": 42,
            "mise_gallery_id": 7,
            "gallery_title": "Spring menu",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["pack_id"] is None
    assert body["job_id"]
    assert body["job_status"] == "queued"
    assert body["recipe_slug"] == "monthly-retainer"

    job = db.one("SELECT * FROM jobs WHERE id=?", (body["job_id"],))
    assert job["status"] == "queued"
    assert jobs.drain(limit=1) == 1
    done = db.one("SELECT * FROM jobs WHERE id=?", (body["job_id"],))
    pack = db.one("SELECT * FROM content_packs WHERE id=?", (done["result_pack_id"],))
    data = json.loads(pack["body_json"])
    assert data["provenance"]["engine"] == "dionysus-argus-enriched"
    assert data["provenance"]["argus_run_id"] == 42
    assert any("plating" in cap for cap in data["captions"])

    campaign = db.one("SELECT * FROM campaigns WHERE id=?", (body["campaign_id"],))
    assert "gallery 7" in campaign["title"]
    assert "Spring menu" in campaign["title"]