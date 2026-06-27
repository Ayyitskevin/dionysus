"""Argus-pack idempotency + correlation_id echo (mock Argus HTTP only)."""

from fastapi.testclient import TestClient

from app import argus, db, jobs
from app.main import app
from tests.test_app import configure_tmp_db, signup

HEADERS = {"Authorization": "Bearer mise-test"}


def _fake_context(run_id: int):
    return {
        "run_id": run_id,
        "photo_count": 2,
        "top_keywords": ["plating", "cocktail"],
        "top_shot_types": ["hero dish"],
        "hero_frames": ["hero dish — plating"],
    }


def _post_argus(client, **extra):
    body = {"argus_run_id": 42, "mise_gallery_id": 7, "gallery_title": "Spring menu"}
    body.update(extra)
    return client.post("/api/mise/organizations/blue-plate/argus-pack",
                       headers=HEADERS, json=body)


def test_argus_pack_is_idempotent_across_retries(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(argus, "fetch_run_context", _fake_context)
    client = TestClient(app)
    signup(client)

    first = _post_argus(client, correlation_id="mise-req-1").json()
    second = _post_argus(client, correlation_id="mise-req-1").json()

    # retry before the worker runs reuses the same campaign + job, no duplicate
    assert first["campaign_id"] == second["campaign_id"]
    assert first["job_id"] == second["job_id"]
    assert second["pack_id"] is None
    campaign_id = first["campaign_id"]

    jobs.drain()
    third = _post_argus(client, correlation_id="mise-req-1").json()

    # retry after completion returns the same pack, still no duplicate
    assert third["job_id"] == first["job_id"]
    assert third["campaign_id"] == campaign_id
    assert third["pack_id"] is not None
    assert third["job_status"] == "done"

    pack_count = db.one(
        "SELECT COUNT(*) AS n FROM content_packs WHERE campaign_id=?",
        (campaign_id,))["n"]
    assert pack_count == 1

    job_count = db.one(
        "SELECT COUNT(*) AS n FROM jobs WHERE kind='generate_pack' AND org_id=?"
        " AND payload LIKE '%\"argus_run_id\": 42%'",
        (db.one("SELECT org_id FROM campaigns WHERE id=?", (campaign_id,))["org_id"],))["n"]
    assert job_count == 1


def test_argus_pack_echoes_correlation_id(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(argus, "fetch_run_context", _fake_context)
    client = TestClient(app)
    signup(client)

    with_id = _post_argus(client, correlation_id="abc-123").json()
    assert with_id["correlation_id"] == "abc-123"

    # no correlation_id supplied -> key omitted (different run to stay idempotent-clean)
    without = _post_argus(client, argus_run_id=43).json()
    assert "correlation_id" not in without


def test_print_pitch_echoes_correlation_id(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    res = client.post(
        "/api/mise/organizations/blue-plate/print-pitch",
        headers=HEADERS,
        json={
            "gallery_name": "Seasonal Tasting Menu",
            "photo_count": 6,
            "estimated_total_cents": 65500,
            "bundles": [{"title": "Wall piece", "pitch": "Lead with your hero."}],
            "correlation_id": "pitch-req-9",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["correlation_id"] == "pitch-req-9"


def test_print_pitch_rejects_non_string_correlation_id(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    res = client.post(
        "/api/mise/organizations/blue-plate/print-pitch",
        headers=HEADERS,
        json={
            "gallery_name": "X",
            "photo_count": 1,
            "estimated_total_cents": 100,
            "bundles": [],
            "correlation_id": 123,
        },
    )
    assert res.status_code == 400
