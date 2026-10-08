import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mandate.anomaly import HISTORY_WINDOW, PaymentSnapshot
from mandate.embeddings import HashingEmbedder
from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest
from mandate.reputation import ReputationIndex, load_records
from mandate.risk_agent import RiskAgent

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class StaticHistory:
    def __init__(self, snapshots: list[PaymentSnapshot] | None = None) -> None:
        self.snapshots = snapshots or []
        self.calls: list[tuple[str, datetime]] = []

    def recent_payments(self, mandate_id: str, since: datetime) -> list[PaymentSnapshot]:
        self.calls.append((mandate_id, since))
        return self.snapshots


def make_mandate() -> Mandate:
    return Mandate(
        mandate_id="m-1",
        issuer="user-1",
        agent_id="agent-1",
        budget_total=Decimal("1000"),
        per_txn_cap=Decimal("100"),
        approval_threshold=Decimal("20"),
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
    )


def make_request(merchant: str, category: str, amount: str = "10") -> PaymentRequest:
    return PaymentRequest(
        request_id="r-1",
        mandate_id="m-1",
        agent_id="agent-1",
        amount=Decimal(amount),
        merchant=merchant,
        category=category,
    )


def make_agent(history: StaticHistory | None = None) -> RiskAgent:
    index = ReputationIndex(load_records(), HashingEmbedder(), use_faiss=False)
    return RiskAgent(index, history or StaticHistory(), clock=lambda: NOW)


def review(merchant: str, category: str, amount: str = "10", history: StaticHistory | None = None):  # type: ignore[no-untyped-def]
    agent = make_agent(history)
    return agent.review(make_mandate(), make_request(merchant, category, amount), Decimal("0"))


def test_trusted_merchant_is_approved() -> None:
    vote = review("Staples", "office_supplies")
    assert vote.decision == Decision.APPROVE
    assert vote.details["reputation"]["source"] == "exact"


def test_risky_merchant_is_escalated() -> None:
    assert review("CryptoExchange", "finance").decision == Decision.ESCALATE
    assert review("BetKing", "gambling").decision == Decision.ESCALATE


def test_known_blocked_merchant_is_denied() -> None:
    assert review("amaz0n-deals", "retail").decision == Decision.DENY


def test_lookalike_of_blocked_merchant_is_denied() -> None:
    vote = review("paypal-support", "payments")
    assert vote.decision == Decision.DENY
    assert any("paypa1-support" in reason for reason in vote.reasons)


def test_unknown_merchant_alone_is_approved() -> None:
    vote = review("Zorblax Inc", "misc")
    assert vote.decision == Decision.APPROVE
    assert vote.details["reputation"]["source"] == "unknown"
    assert any("not found" in reason for reason in vote.reasons)


def test_unknown_merchant_plus_anomalies_is_escalated() -> None:
    history = StaticHistory(
        [PaymentSnapshot(Decimal("10"), "BigMart", NOW - timedelta(hours=2), "executed")]
    )
    vote = review("Zorblax Inc", "misc", amount="19.50", history=history)
    assert vote.decision == Decision.ESCALATE
    names = {a["name"] for a in vote.details["anomalies"]}
    assert {"new_merchant", "near_limit"} <= names


def test_history_is_requested_for_the_mandate_over_the_window() -> None:
    history = StaticHistory()
    review("Staples", "office_supplies", history=history)
    assert history.calls == [("m-1", NOW - HISTORY_WINDOW)]


def test_details_are_json_serializable_and_bounded() -> None:
    vote = review("CryptoExchange", "finance")
    json.dumps(vote.details)
    assert 0 <= vote.details["risk"] <= 1
    assert len(vote.details["reputation"]["matches"]) <= 3
    assert vote.details["backend"] == "numpy"


def test_trusted_merchant_is_not_blocked_by_mild_anomalies() -> None:
    vote = review("Staples", "office_supplies", amount="19.50")
    assert vote.decision == Decision.APPROVE
