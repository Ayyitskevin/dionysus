"""Plutus → Dionysus print-pitch service API."""

from fastapi.testclient import TestClient

from app.main import app
from tests.test_app import configure_tmp_db, signup


def test_print_pitch_is_bearer_gated(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.post(
        "/api/mise/organizations/blue-plate/print-pitch",
        json={"gallery_name": "June", "bundles": [], "photo_count": 1, "estimated_total_cents": 100},
    )
    assert res.status_code == 401


def test_print_pitch_enriches_bundle_copy(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)

    res = client.post(
        "/api/mise/organizations/blue-plate/print-pitch",
        headers={"Authorization": "Bearer mise-test"},
        json={
            "gallery_name": "Seasonal Tasting Menu",
            "photo_count": 6,
            "estimated_total_cents": 65500,
            "gallery_theme": "food",
            "argus_run_id": 219,
            "bundles": [{
                "title": "Statement wall piece",
                "pitch": "Lead with your strongest hero.",
                "items": [{
                    "label": "Canvas",
                    "size": "16x20",
                    "photo": {
                        "filename": "hero.jpg",
                        "keywords": ["risotto", "chef", "warm light"],
                    },
                }],
            }],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["org"] == "blue-plate"
    assert "Seasonal Tasting Menu" in body["intro"]
    assert "Keywords that sell the story" in body["bundles"][0]["pitch"]
    assert body["provenance"]["engine"] == "dionysus-print-pitch"
    assert body["provenance"]["argus_run_id"] == 219
    assert body["estimated_total_cents"] == 65500


def test_print_pitch_rejects_unknown_org(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    res = client.post(
        "/api/mise/organizations/no-such-org/print-pitch",
        headers={"Authorization": "Bearer mise-test"},
        json={
            "gallery_name": "X",
            "bundles": [],
            "photo_count": 1,
            "estimated_total_cents": 100,
        },
    )
    assert res.status_code == 404