import hashlib
import json
import threading
from pathlib import Path

from mandate.llm import LLMClient


class CachingLLM:
    def __init__(self, inner: LLMClient, path: Path, salt: str = "") -> None:
        self._inner = inner
        self._path = path
        self._salt = salt
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self.hits = 0
        self.misses = 0
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    record = json.loads(line)
                    self._cache[record["key"]] = record["value"]

    def complete_json(self, system: str, user: str) -> str:
        key = hashlib.sha256(f"{self._salt}\x00{system}\x00{user}".encode()).hexdigest()
        with self._lock:
            if key in self._cache:
                self.hits += 1
                return self._cache[key]
        value = self._inner.complete_json(system, user)
        with self._lock:
            self._cache[key] = value
            self.misses += 1
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as handle:
                handle.write(json.dumps({"key": key, "value": value}) + "\n")
        return value
