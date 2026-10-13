import importlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from mandate.embeddings import Embedder
from mandate.policy import PaymentRequest

Reputation = Literal["trusted", "neutral", "risky", "blocked"]

RISK_BY_REPUTATION: dict[str, float] = {
    "trusted": 0.0,
    "neutral": 0.25,
    "risky": 0.7,
    "blocked": 1.0,
}

DEFAULT_CORPUS = Path(__file__).parent / "data" / "merchants.jsonl"


class MerchantRecord(BaseModel):
    name: str
    category: str
    reputation: Reputation
    notes: str = ""


class Match(BaseModel):
    record: MerchantRecord
    similarity: float


def normalize_name(name: str) -> str:
    return re.sub(r"[^0-9a-z\u0080-\U0010ffff]", "", name.lower())


def edit_distance(first: str, second: str) -> int:
    rows = len(first) + 1
    cols = len(second) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if first[i - 1] == second[j - 1] else 1
            table[i][j] = min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and first[i - 1] == second[j - 2] and first[i - 2] == second[j - 1]:
                table[i][j] = min(table[i][j], table[i - 2][j - 2] + 1)
    return table[-1][-1]


def load_records(path: Path = DEFAULT_CORPUS) -> list[MerchantRecord]:
    lines = path.read_text().splitlines()
    return [MerchantRecord.model_validate(json.loads(line)) for line in lines if line.strip()]


def record_text(record: MerchantRecord) -> str:
    return record.name


def query_text(request: PaymentRequest) -> str:
    return request.merchant


class ReputationIndex:
    def __init__(
        self,
        records: list[MerchantRecord],
        embedder: Embedder,
        use_faiss: bool | None = None,
    ) -> None:
        self.records = list(records)
        self._embedder = embedder
        self._vectors = embedder.embed([record_text(record) for record in self.records])
        self._faiss_index: Any = None
        if use_faiss is not False:
            try:
                faiss = importlib.import_module("faiss")
            except ImportError:
                if use_faiss:
                    raise
            else:
                self._faiss_index = faiss.IndexFlatIP(self._vectors.shape[1])
                self._faiss_index.add(self._vectors)

    @property
    def backend(self) -> str:
        return "faiss" if self._faiss_index is not None else "numpy"

    def search(self, query: str, k: int = 5) -> list[Match]:
        if not self.records:
            return []
        k = min(k, len(self.records))
        vector = self._embedder.embed([query])
        if self._faiss_index is not None:
            scores, ids = self._faiss_index.search(vector, k)
            pairs = [(int(i), float(s)) for i, s in zip(ids[0], scores[0], strict=True) if i >= 0]
        else:
            similarities = self._vectors @ vector[0]
            order = np.argsort(-similarities)[:k]
            pairs = [(int(i), float(similarities[i])) for i in order]
        return [Match(record=self.records[i], similarity=score) for i, score in pairs]
