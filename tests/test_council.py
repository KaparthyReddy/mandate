import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from mandate.council import Council
from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest
from mandate.reviewers import Vote

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def make_mandate() -> Mandate:
    return Mandate(
        mandate_id="m-1",
        issuer="user-1",
        agent_id="agent-1",
        budget_total=Decimal("100"),
        per_txn_cap=Decimal("50"),
        approval_threshold=Decimal("20"),
        issued_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def make_request() -> PaymentRequest:
    return PaymentRequest(
        request_id="r-1",
        mandate_id="m-1",
        agent_id="agent-1",
        amount=Decimal("10"),
        merchant="BigMart",
        category="groceries",
    )


class StubAgent:
    def __init__(self, name: str, decision: Decision, calls: list[str] | None = None) -> None:
        self.name = name
        self.decision = decision
        self.calls = calls if calls is not None else []

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote:
        self.calls.append(self.name)
        return Vote(reviewer=self.name, decision=self.decision, reasons=[f"{self.name} says so"])


class CrashingAgent:
    name = "crash"

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote:
        raise RuntimeError("boom")


def review(council: Council) -> Vote:
    return council.review(make_mandate(), make_request(), Decimal("0"))


def test_empty_council_approves() -> None:
    assert review(Council([])).decision == Decision.APPROVE


def test_all_agents_run() -> None:
    calls: list[str] = []
    council = Council(
        [StubAgent("a", Decision.APPROVE, calls), StubAgent("b", Decision.APPROVE, calls)]
    )
    vote = review(council)
    assert vote.decision == Decision.APPROVE
    assert sorted(calls) == ["a", "b"]


def test_escalate_beats_approve() -> None:
    council = Council([StubAgent("a", Decision.APPROVE), StubAgent("b", Decision.ESCALATE)])
    assert review(council).decision == Decision.ESCALATE


def test_deny_beats_escalate() -> None:
    council = Council([StubAgent("a", Decision.ESCALATE), StubAgent("b", Decision.DENY)])
    vote = review(council)
    assert vote.decision == Decision.DENY
    assert any("b says so" in reason for reason in vote.reasons)


def test_crashing_agent_fails_safe() -> None:
    council = Council([StubAgent("a", Decision.APPROVE), CrashingAgent()])
    vote = review(council)
    assert vote.decision == Decision.ESCALATE
    assert any("agent failed" in reason for reason in vote.reasons)


def test_details_list_votes_in_agent_order() -> None:
    council = Council([StubAgent("a", Decision.APPROVE), StubAgent("b", Decision.DENY)])
    votes = review(council).details["votes"]
    assert [v["reviewer"] for v in votes] == ["a", "b"]


def test_duplicate_agent_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        Council([StubAgent("a", Decision.APPROVE), StubAgent("a", Decision.DENY)])


def test_agents_run_in_parallel() -> None:
    barrier = threading.Barrier(2, timeout=5)

    class WaitingAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote:
            barrier.wait()
            return Vote(reviewer=self.name, decision=Decision.APPROVE, reasons=[])

    vote = review(Council([WaitingAgent("a"), WaitingAgent("b")]))
    assert vote.decision == Decision.APPROVE
