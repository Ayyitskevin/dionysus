"""Local model client — OpenAI-compatible /v1/chat/completions, mock-only."""

import json

import httpx

from app import config, model_client


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _enable(monkeypatch, *, key=""):
    monkeypatch.setattr(config, "MODEL_ENDPOINT", "http://local/v1")
    monkeypatch.setattr(config, "MODEL_NAME", "llama3.1:8b")
    monkeypatch.setattr(config, "MODEL_API_KEY", key)


def test_is_enabled_requires_endpoint_and_name(monkeypatch):
    monkeypatch.setattr(config, "MODEL_ENDPOINT", "")
    monkeypatch.setattr(config, "MODEL_NAME", "")
    assert model_client.is_enabled() is False
    monkeypatch.setattr(config, "MODEL_ENDPOINT", "http://local/v1")
    assert model_client.is_enabled() is False  # name still empty
    monkeypatch.setattr(config, "MODEL_NAME", "m")
    assert model_client.is_enabled() is True


def test_disabled_returns_none_without_calling(monkeypatch):
    monkeypatch.setattr(config, "MODEL_ENDPOINT", "")
    monkeypatch.setattr(config, "MODEL_NAME", "")

    def handler(request):  # pragma: no cover - must not run
        raise AssertionError("disabled client must not hit the network")

    assert model_client.complete([{"role": "user", "content": "hi"}],
                                 client=_mock_client(handler)) is None


def test_enabled_shapes_request_and_parses_reply(monkeypatch):
    _enable(monkeypatch, key="secret")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama3.1:8b",
            "choices": [{"message": {"role": "assistant", "content": " hi there "}}],
        })

    res = model_client.complete([{"role": "user", "content": "hi"}],
                                client=_mock_client(handler))
    assert res["text"] == "hi there"
    assert res["model"] == "llama3.1:8b"
    assert res["cost_usd"] == 0.0
    assert res["latency_ms"] >= 0
    assert seen["url"] == "http://local/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["stream"] is False
    assert seen["body"]["model"] == "llama3.1:8b"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_no_auth_header_without_key(monkeypatch):
    _enable(monkeypatch, key="")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}]})

    model_client.complete([{"role": "user", "content": "hi"}],
                          client=_mock_client(handler))
    assert seen["auth"] is None


def test_request_carries_sampling_params_and_overrides(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "MODEL_TEMPERATURE", 0.4)
    monkeypatch.setattr(config, "MODEL_MAX_TOKENS", 800)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _mock_client(handler)
    # defaults flow from config
    model_client.complete([{"role": "user", "content": "hi"}], client=client)
    assert bodies[-1]["temperature"] == 0.4
    assert bodies[-1]["max_tokens"] == 800
    # explicit per-call overrides flow through, including falsy values (temperature=0)
    model_client.complete([{"role": "user", "content": "hi"}],
                          temperature=0, max_tokens=128, client=client)
    assert bodies[-1]["temperature"] == 0
    assert bodies[-1]["max_tokens"] == 128


def test_http_error_returns_none(monkeypatch):
    _enable(monkeypatch)

    def handler(request):
        return httpx.Response(500, text="boom")

    assert model_client.complete([{"role": "user", "content": "hi"}],
                                 client=_mock_client(handler)) is None


def test_timeout_returns_none(monkeypatch):
    _enable(monkeypatch)

    def handler(request):
        raise httpx.ReadTimeout("timed out")

    assert model_client.complete([{"role": "user", "content": "hi"}],
                                 client=_mock_client(handler)) is None


def test_network_error_returns_none(monkeypatch):
    _enable(monkeypatch)

    def handler(request):
        raise httpx.ConnectError("connection refused")

    assert model_client.complete([{"role": "user", "content": "hi"}],
                                 client=_mock_client(handler)) is None


def test_empty_content_returns_none(monkeypatch):
    _enable(monkeypatch)

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    assert model_client.complete([{"role": "user", "content": "hi"}],
                                 client=_mock_client(handler)) is None


def test_malformed_json_returns_none(monkeypatch):
    _enable(monkeypatch)

    def handler(request):
        return httpx.Response(200, text="not json", headers={"content-type": "application/json"})

    assert model_client.complete([{"role": "user", "content": "hi"}],
                                 client=_mock_client(handler)) is None
