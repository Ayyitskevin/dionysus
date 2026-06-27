"""Model-backed pack generation with deterministic fallback (mock-only)."""

import json

from fastapi.testclient import TestClient

from app import config, db, generator, jobs, model_client
from app.main import app
from tests.test_app import configure_tmp_db, signup


# --- parser units --------------------------------------------------------------

def test_parse_model_pack_plain_json():
    parsed = generator._parse_model_pack(
        '{"strategy": "Lead with the tasting menu.", "captions": ["A", "B"]}')
    assert parsed == {"strategy": "Lead with the tasting menu.", "captions": ["A", "B"]}


def test_parse_model_pack_fenced_json():
    text = '```json\n{"strategy": "S", "captions": ["c1", "c2"]}\n```'
    parsed = generator._parse_model_pack(text)
    assert parsed["strategy"] == "S"
    assert parsed["captions"] == ["c1", "c2"]


def test_parse_model_pack_with_surrounding_prose():
    text = 'Sure! Here is the JSON: {"strategy": "S", "captions": ["c"]} hope it helps'
    assert generator._parse_model_pack(text) == {"strategy": "S", "captions": ["c"]}


def test_parse_model_pack_ignores_trailing_prose_with_braces():
    # The object is followed by prose that itself contains a brace; raw_decode
    # must still recover the leading object instead of over-spanning to the last }.
    text = 'Here: {"strategy": "S", "captions": ["c"]} and also {a note}'
    assert generator._parse_model_pack(text) == {"strategy": "S", "captions": ["c"]}


def test_parse_model_pack_rejects_incomplete():
    assert generator._parse_model_pack('{"strategy": "", "captions": []}') is None
    assert generator._parse_model_pack("not json at all") is None
    assert generator._parse_model_pack('{"strategy": "ok"}') is None  # no captions


def test_parse_model_pack_caps_captions():
    captions = [f"c{i}" for i in range(20)]
    parsed = generator._parse_model_pack(
        json.dumps({"strategy": "s", "captions": captions}))
    assert len(parsed["captions"]) == generator._MAX_MODEL_CAPTIONS


# --- end-to-end generation -----------------------------------------------------

def _enable_model(monkeypatch):
    monkeypatch.setattr(config, "MODEL_ENDPOINT", "http://local/v1")
    monkeypatch.setattr(config, "MODEL_NAME", "llama3.1:8b")


def test_generate_uses_model_when_enabled(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    _enable_model(monkeypatch)

    def fake_complete(messages, **kwargs):
        return {
            "text": json.dumps({
                "strategy": "Model-written strategy.",
                "captions": ["Model caption one.", "Model caption two."],
            }),
            "model": "llama3.1:8b",
            "latency_ms": 42,
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(model_client, "complete", fake_complete)
    client = TestClient(app)
    signup(client)  # enqueues + drains the seed generate job

    pack = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    body = json.loads(pack["body_json"])
    assert body["strategy"] == "Model-written strategy."
    assert body["captions"] == ["Model caption one.", "Model caption two."]
    assert pack["ai_model"] == "llama3.1:8b"
    assert body["provenance"]["engine"] == "dionysus-local-model"
    assert body["provenance"]["model"] == "llama3.1:8b"
    assert body["provenance"]["latency_ms"] == 42

    res = client.get(
        "/api/mise/organizations/blue-plate/packs?include_drafts=true",
        headers={"Authorization": "Bearer mise-test"})
    env = res.json()["packs"][0]["contract"]
    assert env["model"] == "llama3.1:8b"
    assert env["latency_ms"] == 42
    assert env["cost_usd"] == 0.0


def test_generate_falls_back_when_model_fails(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    _enable_model(monkeypatch)
    monkeypatch.setattr(model_client, "complete", lambda *a, **k: None)
    client = TestClient(app)
    signup(client)

    pack = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    body = json.loads(pack["body_json"])
    assert body["provenance"]["engine"] != "dionysus-local-model"
    assert pack["ai_model"] == body["provenance"]["engine"]


def test_generate_falls_back_when_model_raises(tmp_path, monkeypatch):
    # The contract's load-bearing guarantee: an exception in drafting must NOT
    # crash the job; it falls back to the deterministic pack and writes it.
    configure_tmp_db(tmp_path, monkeypatch)
    _enable_model(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(model_client, "complete", boom)
    client = TestClient(app)
    signup(client)

    assert jobs.failed_count() == 0
    pack = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    body = json.loads(pack["body_json"])
    assert body["provenance"]["engine"] != "dionysus-local-model"
    assert pack["ai_model"] == body["provenance"]["engine"]


def test_generate_falls_back_on_unparseable_reply(tmp_path, monkeypatch):
    # 200 OK but the model returned prose, not JSON -> deterministic fallback.
    configure_tmp_db(tmp_path, monkeypatch)
    _enable_model(monkeypatch)

    def fake_complete(messages, **kwargs):
        return {"text": "I cannot help with that", "model": "llama3.1:8b",
                "latency_ms": 10, "cost_usd": 0.0}

    monkeypatch.setattr(model_client, "complete", fake_complete)
    client = TestClient(app)
    signup(client)

    pack = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    body = json.loads(pack["body_json"])
    assert body["provenance"]["engine"] != "dionysus-local-model"


def test_healthz_never_leaks_api_key(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MODEL_ENDPOINT", "http://local/v1")
    monkeypatch.setattr(config, "MODEL_NAME", "llama3.1:8b")
    monkeypatch.setattr(config, "MODEL_API_KEY", "super-secret-key")
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "super-secret-key" not in res.text
    # the model block exposes only non-secret status, never the key/endpoint URL
    assert res.json()["model"] == {
        "enabled": True, "name": "llama3.1:8b", "endpoint_configured": True}


def test_generate_skips_model_when_disabled(tmp_path, monkeypatch):
    configure_tmp_db(tmp_path, monkeypatch)
    # endpoint unset -> is_enabled() is False -> complete() is never reached
    calls = {"n": 0}

    def tripwire(*args, **kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr(model_client, "complete", tripwire)
    client = TestClient(app)
    signup(client)

    assert calls["n"] == 0
    pack = db.one("SELECT * FROM content_packs ORDER BY id DESC LIMIT 1")
    assert json.loads(pack["body_json"])["provenance"]["engine"] != "dionysus-local-model"
