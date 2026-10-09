import importlib
import json
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
