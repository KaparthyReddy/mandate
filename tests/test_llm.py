from typing import Any

import pytest
import requests

from mandate.llm import FakeLLM, LLMError, OllamaClient


class FakeResponse:
    def __init__(self, ok: bool = True, status_code: int = 200, body: Any = None) -> None:
        self.ok = ok
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_ollama_client_returns_message_content(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return FakeResponse(body={"message": {"content": '{"verdict": "match"}'}})

    monkeypatch.setattr(requests, "post", fake_post)
    client = OllamaClient("http://localhost:11434/", "qwen2.5:7b-instruct")
    assert client.complete_json("sys", "user") == '{"verdict": "match"}'
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["json"]["format"] == "json"
    assert captured["json"]["stream"] is False
    assert captured["json"]["options"]["temperature"] == 0
    assert captured["json"]["model"] == "qwen2.5:7b-instruct"


def test_ollama_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        requests, "post", lambda url, **kw: FakeResponse(ok=False, status_code=500, body="bad")
    )
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m").complete_json("s", "u")


def test_ollama_unreachable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **kwargs: Any) -> FakeResponse:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m").complete_json("s", "u")


def test_ollama_malformed_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "post", lambda url, **kw: FakeResponse(body={"oops": 1}))
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m").complete_json("s", "u")


def test_fake_llm_records_calls() -> None:
    llm = FakeLLM("{}")
    assert llm.complete_json("a", "b") == "{}"
    assert llm.calls == [("a", "b")]
