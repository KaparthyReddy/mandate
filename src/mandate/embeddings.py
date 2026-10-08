import importlib
import logging
import re
import zlib
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from mandate.config import Settings

logger = logging.getLogger(__name__)

Vectors = NDArray[np.float32]

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> Vectors: ...


class HashingEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        features: list[str] = []
        for token in _TOKEN.findall(_CAMEL.sub(" ", text).lower()):
            features.append(f"w:{token}")
            padded = f"^{token}$"
            features.extend(f"c:{padded[i : i + 3]}" for i in range(len(padded) - 2))
        return features

    def embed(self, texts: list[str]) -> Vectors:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in self._features(text):
                digest = zlib.crc32(feature.encode())
                out[row, digest % self.dim] += -1.0 if (digest >> 16) & 1 else 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return np.asarray(out / norms, dtype=np.float32)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        module = importlib.import_module("sentence_transformers")
        self._model: Any = module.SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> Vectors:
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(settings: Settings) -> Embedder:
    kind = settings.embedder
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(settings.embedding_model)
    try:
        return SentenceTransformerEmbedder(settings.embedding_model)
    except Exception as exc:
        logger.warning("sentence-transformers unavailable (%s); using hashing embedder", exc)
        return HashingEmbedder()
