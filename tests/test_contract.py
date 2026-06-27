"""Worker-Contract draft envelope — mapping + schema validation."""

import pytest
from fastapi.testclient import TestClient

from app import contract
from app.main import app
from tests.test_app import configure_tmp_db, signup


# --- pure mapping / validation -------------------------------------------------

def test_draft_drops_empty_optionals_and_strips():
    item = contract.draft("caption", "  hello  ", title="  ", alt_text=" ok ")
    assert item == {"kind": "caption", "body": "hello", "alt_text": "ok"}
    assert "title" not in item


def test_draft_rejects_unknown_kind_and_empty_body():
    with pytest.raises(ValueError):
        contract.draft("headline", "x")
    with pytest.raises(ValueError):
        contract.draft("caption", "   ")


def test_envelope_defaults_to_local_zero_cost():
    env = contract.envelope([contract.draft("email", "hi")])
    assert env["model"] == contract.LOCAL_MODEL
    assert env["cost_usd"] == 0.0
    assert env["latency_ms"] == 0
    contract.validate_envelope(env)


def test_drafts_from_pack_yields_pack_plus_captions():
    body = {
        "headline": "June content pack",
        "strategy": "Lead with the tasting menu.",
        "shot_list": ["Hero plate", "Hands plating"],
        "captions": ["Friday tables fill fast.", "New this week."],
        "exports": ["instagram: hook"],
        "upsells": ["Book the refresh."],
    }
    drafts = contract.drafts_from_pack(body, title="June content pack")
    kinds = [d["kind"] for d in drafts]
    assert kinds == ["campaign_pack", "caption", "caption"]
    pack_draft = drafts[0]
    assert pack_draft["title"] == "June content pack"
    assert "Hero plate" in pack_draft["body"]
    assert "Book the refresh." in pack_draft["body"]
    contract.validate_envelope(contract.envelope(drafts))


def test_drafts_from_pack_falls_back_to_headline_when_sparse():
    drafts = contract.drafts_from_pack({"headline": "Bare pack"})
    assert drafts == [{"kind": "campaign_pack", "title": "Bare pack", "body": "Bare pack"}]


def test_drafts_from_print_pitch_maps_intro_and_bundles():
    pitch = {
        "intro": "Your gallery is ready.",
        "bundles": [
            {"title": "Statement wall piece", "pitch": "Lead with your hero."},
            {"title": "", "pitch": ""},  # dropped
        ],
    }
    drafts = contract.drafts_from_print_pitch(pitch)
    assert [d["kind"] for d in drafts] == ["email", "email"]
    assert drafts[0]["title"] == "Gallery print pitch"
    assert drafts[1]["title"] == "Statement wall piece"


@pytest.mark.parametrize("bad", [
    {"model": "m", "latency_ms": 0, "cost_usd": 0.0, "drafts": []},  # empty
    {"model": "", "latency_ms": 0, "cost_usd": 0.0,
     "drafts": [{"kind": "email", "body": "x"}]},  # blank model
    {"model": "m", "latency_ms": -1, "cost_usd": 0.0,
     "drafts": [{"kind": "email", "body": "x"}]},  # negative latency
    {"model": "m", "latency_ms": 0, "cost_usd": -0.1,
     "drafts": [{"kind": "email", "body": "x"}]},  # negative cost
    {"model": "m", "latency_ms": 0, "cost_usd": 0.0,
     "drafts": [{"kind": "headline", "body": "x"}]},  # bad kind
    {"model": "m", "latency_ms": 0, "cost_usd": 0.0,
     "drafts": [{"kind": "email", "body": "x", "extra": 1}]},  # unexpected key
    {"model": "m", "latency_ms": 0, "cost_usd": 0.0},  # missing drafts
])
def test_validate_envelope_rejects_invalid(bad):
    with pytest.raises(ValueError):
        contract.validate_envelope(bad)


# --- exposed on the Mise-facing endpoints (backward-compatible addition) -------

def test_print_pitch_response_includes_valid_contract(tmp_path, monkeypatch):
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
            "bundles": [{"title": "Statement wall piece",
                         "pitch": "Lead with your strongest hero."}],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # old fields preserved
    assert body["intro"]
    assert body["provenance"]["engine"] == "dionysus-print-pitch"
    # new contract envelope is valid
    env = body["contract"]
    contract.validate_envelope(env)
    assert env["model"] == contract.LOCAL_MODEL
    assert env["cost_usd"] == 0.0
    assert any(d["kind"] == "email" for d in env["drafts"])


def test_packs_api_includes_valid_contract(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    client = TestClient(app)
    signup(client)  # generates an initial draft pack
    res = client.get(
        "/api/mise/organizations/blue-plate/packs?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"},
    )
    assert res.status_code == 200, res.text
    packs = res.json()["packs"]
    assert packs, "signup should have generated a pack"
    env = packs[0]["contract"]
    contract.validate_envelope(env)
    kinds = {d["kind"] for d in env["drafts"]}
    assert "campaign_pack" in kinds
    assert "caption" in kinds
    assert env["cost_usd"] == 0.0
