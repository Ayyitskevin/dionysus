"""Human-edit guard: Mise flags a draft human-edited; machine drafts never clobber."""

from fastapi.testclient import TestClient

from app import argus, db, jobs
from app.main import app
from tests.test_app import configure_tmp_db, signup

HEADERS = {"Authorization": "Bearer mise-test"}


def _latest_pack():
    return db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")


def _mark_human_edited(client, pack_id):
    return client.post(
        f"/api/mise/organizations/blue-plate/packs/{pack_id}/human-edited",
        headers=HEADERS,
    )


def test_fresh_generated_pack_is_not_human_edited(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    assert _latest_pack()["human_edited"] == 0


def test_mark_human_edited_sets_flag_and_api_exposes_it(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = _latest_pack()

    res = _mark_human_edited(client, pack["id"])
    assert res.status_code == 200, res.text
    assert res.json()["human_edited"] is True
    assert db.one("SELECT human_edited FROM content_packs WHERE id=?",
                  (pack["id"],))["human_edited"] == 1

    api = client.get("/api/mise/organizations/blue-plate/packs?include_drafts=true",
                     headers=HEADERS)
    assert api.status_code == 200, api.text
    payload = next(p for p in api.json()["packs"] if p["id"] == pack["id"])
    assert payload["human_edited"] is True


def test_machine_never_clobbers_human_edited_pack(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    source = _latest_pack()

    assert _mark_human_edited(client, source["id"]).status_code == 200
    before = db.one("SELECT body_json, human_edited FROM content_packs WHERE id=?",
                    (source["id"],))

    # A machine generate for a DIFFERENT campaign/run drafts a brand-new pack and
    # never touches the existing human-edited one.
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
        headers=HEADERS,
        json={"argus_run_id": 99, "mise_gallery_id": 3, "gallery_title": "Fall menu"},
    )
    assert res.status_code == 200, res.text
    jobs.drain()

    new_pack = _latest_pack()
    assert new_pack["id"] != source["id"]
    assert new_pack["human_edited"] == 0  # machine draft

    after = db.one("SELECT body_json, human_edited FROM content_packs WHERE id=?",
                   (source["id"],))
    assert after["body_json"] == before["body_json"]  # human body byte-for-byte unchanged
    assert after["human_edited"] == 1
