"""Human-edit guard: revisions are flagged + surfaced; machine drafts never clobber."""

from fastapi.testclient import TestClient

from app import db, jobs
from app.main import app
from tests.test_app import configure_tmp_db, signup


def _latest_pack():
    return db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")


def _revise(client, pack_id, **overrides):
    data = {
        "title": "Human edited rollout",
        "strategy": "Lead with the chef tasting menu.",
        "shot_list": "Hero plate",
        "captions": "A human wrote this caption.",
        "exports": "Instagram: reservation CTA",
        "upsells": "Add delivery crop set",
    }
    data.update(overrides)
    return client.post(f"/w/blue-plate/packs/{pack_id}/revise", data=data,
                       follow_redirects=False)


def test_fresh_generated_pack_is_not_human_edited(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    assert _latest_pack()["human_edited"] == 0


def test_revise_marks_pack_human_edited_and_api_exposes_it(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    pack = _latest_pack()

    res = _revise(client, pack["id"])
    assert res.status_code == 303, res.text
    assert db.one("SELECT human_edited FROM content_packs WHERE id=?",
                  (pack["id"],))["human_edited"] == 1

    api = client.get("/api/mise/organizations/blue-plate/packs?include_drafts=true",
                     headers={"Authorization": "Bearer mise-test"})
    assert api.status_code == 200, api.text
    payload = next(p for p in api.json()["packs"] if p["id"] == pack["id"])
    assert payload["human_edited"] is True


def test_regenerate_makes_new_machine_draft_without_clobbering_human_edit(
        tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)
    source = _latest_pack()

    assert _revise(client, source["id"]).status_code == 303
    before = db.one("SELECT body_json, human_edited FROM content_packs WHERE id=?",
                    (source["id"],))

    res = client.post(f"/w/blue-plate/packs/{source['id']}/regenerate",
                      data={"feedback": "make it more premium"},
                      follow_redirects=False)
    assert res.status_code == 303, res.text
    jobs.drain()

    new_pack = _latest_pack()
    assert new_pack["id"] != source["id"]
    assert new_pack["source_pack_id"] == source["id"]
    assert new_pack["human_edited"] == 0  # machine draft

    after = db.one("SELECT body_json, human_edited FROM content_packs WHERE id=?",
                   (source["id"],))
    assert after["body_json"] == before["body_json"]  # human body untouched
    assert after["human_edited"] == 1
