from typing import Protocol

import requests

from mandate.config import Settings


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> str: ...


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 90.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def complete_json(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": 300},
        }
        try:
            response = requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"Ollama unreachable: {exc}") from exc
        if not response.ok:
            raise LLMError(f"Ollama {response.status_code}: {response.text[:200]}")
        try:
            return str(response.json()["message"]["content"])
        except (KeyError, ValueError) as exc:
            raise LLMError(f"unexpected Ollama response: {exc}") from exc


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


def build_llm(settings: Settings, model: str | None = None) -> LLMClient:
    return OllamaClient(settings.ollama_url, model or settings.ollama_model, settings.llm_timeout)
