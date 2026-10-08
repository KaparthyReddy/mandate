import pytest

from mandate.embeddings import HashingEmbedder
from mandate.reputation import MerchantRecord, ReputationIndex, load_records


def make_index(use_faiss: bool | None = False) -> ReputationIndex:
    return ReputationIndex(load_records(), HashingEmbedder(), use_faiss=use_faiss)


def test_corpus_is_valid_and_names_are_unique() -> None:
    records = load_records()
    names = [record.name.lower() for record in records]
    assert len(records) >= 50
    assert len(names) == len(set(names))
    assert {record.reputation for record in records} == {"trusted", "neutral", "risky", "blocked"}


def test_exact_merchant_is_top_match() -> None:
    top = make_index().search("Staples | office_supplies", k=3)[0]
    assert top.record.name == "Staples"
    assert top.similarity > 0.99


def test_lookalike_matches_blocked_record() -> None:
    top = make_index().search("paypal-support | payments", k=1)[0]
    assert top.record.name == "paypa1-support"
    assert top.record.reputation == "blocked"


def test_unknown_merchant_has_low_similarity() -> None:
    assert make_index().search("Zorblax Inc | misc", k=1)[0].similarity < 0.3


def test_k_larger_than_corpus_is_clamped() -> None:
    records = [MerchantRecord(name="A", category="x", reputation="trusted")]
    index = ReputationIndex(records, HashingEmbedder(), use_faiss=False)
    assert len(index.search("A | x", k=10)) == 1


def test_empty_index_returns_nothing() -> None:
    assert ReputationIndex([], HashingEmbedder(), use_faiss=False).search("a | b") == []


def test_numpy_backend_reported() -> None:
    assert make_index(use_faiss=False).backend == "numpy"


def test_faiss_and_numpy_backends_agree() -> None:
    pytest.importorskip("faiss")
    numpy_index = make_index(use_faiss=False)
    faiss_index = make_index(use_faiss=True)
    assert faiss_index.backend == "faiss"
    for query in ("BetKing | gambling", "AWS | cloud", "CryptoExchange | finance"):
        expected = [m.record.name for m in numpy_index.search(query, k=3)]
        actual = [m.record.name for m in faiss_index.search(query, k=3)]
        assert actual == expected
