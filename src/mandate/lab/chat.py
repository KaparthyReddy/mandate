import re
from typing import Any, Protocol

import requests

from mandate.llm import LLMError


class ChatModel(Protocol):
    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class OllamaChat:
    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 400},
        }
        try:
            response = requests.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise LLMError(f"Ollama unreachable: {exc}") from exc
        if not response.ok:
            raise LLMError(f"Ollama {response.status_code}: {response.text[:200]}")
        try:
            message = response.json()["message"]
        except (KeyError, ValueError) as exc:
            raise LLMError(f"unexpected Ollama response: {exc}") from exc
        return dict(message)


class ScriptedChat:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.seen.append([dict(message) for message in messages])
        if self._replies:
            return self._replies.pop(0)
        return {"role": "assistant", "content": "Done."}


def tool_call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


class GullibleChat:
    """A fully compromised agent: it obeys every instruction found on a product page."""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if not tool_messages:
            task = next(str(m["content"]) for m in messages if m["role"] == "user")
            found = re.search(r"Buy the (.+?) for the", task)
            return tool_call("search_store", query=found.group(1) if found else task)
        last = tool_messages[-1]
        if last.get("tool_name") == "search_store":
            return tool_call("read_product_page", product_id=str(last["content"]).split(" | ")[0])
        if last.get("tool_name") == "read_product_page":
            return tool_call("buy", **self._order(self._target(messages), str(last["content"])))
        return say("Done.")

    @staticmethod
    def _target(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            for call in message.get("tool_calls") or []:
                if call["function"]["name"] == "read_product_page":
                    return str(call["function"]["arguments"]["product_id"])
        return ""

    @staticmethod
    def _order(target: str, page: str) -> dict[str, Any]:
        order: dict[str, Any] = {"product_id": target}
        extra = re.search(r"\bbuy ([A-Z]+-\d+)", page)
        if extra:
            order["product_id"] = extra.group(1)
        units = re.search(r"\bbuy (\d+) units", page)
        if units:
            order["quantity"] = int(units.group(1))
        merchant = re.search(r"merchant ([\w-]+) instead", page)
        if merchant:
            order["pay_to"] = merchant.group(1)
        total = re.search(r"pay (\d+\.\d{2}) for this order", page)
        if total:
            order["total"] = total.group(1)
        return order
