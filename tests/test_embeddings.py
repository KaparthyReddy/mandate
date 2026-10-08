import numpy as np
import pytest
from test_wiring import make_settings

from mandate import embeddings
from mandate.embeddings import HashingEmbedder, build_embedder


def similarity(embedder: HashingEmbedder, first: str, second: str) -> float:
    vectors = embedder.embed([first, second])
    return float(vectors[0] @ vectors[1])


def test_embeddings_are_deterministic() -> None:
    embedder = HashingEmbedder()
    assert np.array_equal(
        embedder.embed(["BigMart | groceries"]), embedder.embed(["BigMart | groceries"])
    )


def test_embeddings_are_unit_length() -> None:
    vectors = HashingEmbedder().embed(["alpha", "beta gamma", "Casino Royale | gambling"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_empty_text_gives_zero_vector_without_nan() -> None:
    vector = HashingEmbedder().embed([""])
    assert not np.isnan(vector).any()
    assert float(np.linalg.norm(vector)) == 0.0


def test_camel_case_names_match_spaced_names() -> None:
    embedder = HashingEmbedder()
    close = similarity(embedder, "CasinoRoyal | gambling", "Casino Royal | gambling")
    far = similarity(embedder, "CasinoRoyal | gambling", "Walmart | groceries")
    assert close > 0.9
    assert far < 0.3


def test_lookalike_names_stay_close() -> None:
    embedder = HashingEmbedder()
    assert similarity(embedder, "paypal-support | payments", "paypa1-support | payments") > 0.8


def test_hashing_embedder_is_selectable() -> None:
    assert isinstance(build_embedder(make_settings(embedder="hashing")), HashingEmbedder)


def test_auto_falls_back_to_hashing_when_sentence_transformers_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(model_name: str) -> None:
        raise ImportError("no sentence-transformers")

    monkeypatch.setattr(embeddings, "SentenceTransformerEmbedder", boom)
    assert isinstance(build_embedder(make_settings(embedder="auto")), HashingEmbedder)
