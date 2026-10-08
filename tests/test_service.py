import copy
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mandate import crypto, ledger, service
from mandate.models import Base, LedgerEntry, MandateRecord, PaymentRecord
from mandate.policy import Decision, PaymentRequest
from mandate.provider import FakeProvider
from mandate.reviewers import Reviewer, Vote
from mandate.service import MandateCreate, NotFoundError, StateError


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'service.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return crypto.generate_private_key()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


class StubReviewer:
    name = "stub"

    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    def review(self, mandate: Any, request: PaymentRequest, spent: Decimal) -> Vote:
        return Vote(reviewer=self.name, decision=self.decision, reasons=["stub says so"])


class BrokenReviewer:
    name = "broken"

    def review(self, mandate: Any, request: PaymentRequest, spent: Decimal) -> Vote:
        raise RuntimeError("boom")


def new_mandate(session: Session, key: Ed25519PrivateKey, **overrides: Any) -> str:
    data: dict[str, Any] = {
        "issuer": "user-1",
        "agent_id": "agent-1",
        "budget_total": Decimal("100.00"),
        "per_txn_cap": Decimal("50.00"),
        "approval_threshold": Decimal("20.00"),
        "allowed_categories": ["groceries"],
    }
    data.update(overrides)
    signed = service.create_mandate(session, MandateCreate(**data), key)
    return signed.mandate.mandate_id


def pay(
    session: Session,
    key: Ed25519PrivateKey,
    provider: FakeProvider,
    mandate_id: str,
    amount: str,
    request_id: str,
    reviewers: Sequence[Reviewer] = (),
    now: datetime | None = None,
    **overrides: Any,
) -> PaymentRecord:
    data: dict[str, Any] = {
        "request_id": request_id,
        "mandate_id": mandate_id,
        "agent_id": "agent-1",
        "amount": Decimal(amount),
        "merchant": "BigMart",
        "category": "groceries",
    }
    data.update(overrides)
    return service.process_payment(
        session, PaymentRequest(**data), key.public_key(), provider, reviewers, now
    )


def entry_types(session: Session) -> list[str]:
    return list(session.scalars(select(LedgerEntry.entry_type).order_by(LedgerEntry.seq)))


def test_approved_payment_creates_order(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "10.00", "r-1")
    assert record.status == "order_created"
    assert record.approval_url
    assert provider.orders == ["FAKE-r-1"]
    assert entry_types(session) == [
        "mandate_issued",
        "payment_requested",
        "decision_made",
        "order_created",
    ]
    assert ledger.verify_chain(session).valid


def test_escalation_then_human_approval(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "30.00", "r-1")
    assert record.status == "pending_approval"
    assert provider.orders == []
    approved = service.approve_payment(session, "r-1", key.public_key(), provider)
    assert approved.status == "order_created"
    assert len(provider.orders) == 1


def test_denied_over_cap(session: Session, key: Ed25519PrivateKey, provider: FakeProvider) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "60.00", "r-1")
    assert record.status == "denied"
    assert record.decision == "deny"
    assert record.reasons
    assert provider.orders == []


def test_budget_splitting_is_blocked(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key, approval_threshold=Decimal("50.00"))
    first = pay(session, key, provider, mid, "40.00", "r-1")
    second = pay(session, key, provider, mid, "40.00", "r-2")
    third = pay(session, key, provider, mid, "40.00", "r-3")
    assert first.status == "order_created"
    assert second.status == "order_created"
    assert third.status == "denied"
    assert service.spent_amount(session, mid) == Decimal("80.00")


def test_duplicate_request_id_is_idempotent(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    first = pay(session, key, provider, mid, "10.00", "r-1")
    second = pay(session, key, provider, mid, "10.00", "r-1")
    assert first.id == second.id
    assert len(provider.orders) == 1
    assert service.spent_amount(session, mid) == Decimal("10.00")


def test_revoked_mandate_denies(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    service.revoke_mandate(session, mid)
    record = pay(session, key, provider, mid, "10.00", "r-1")
    assert record.status == "denied"
    assert any("revoked" in reason for reason in record.reasons)


def test_unknown_mandate_denies(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    record = pay(session, key, provider, "m-nope", "10.00", "r-1")
    assert record.status == "denied"
    assert any("unknown" in reason for reason in record.reasons)


def test_tampered_stored_mandate_denies(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    stored = session.get(MandateRecord, mid)
    assert stored is not None
    forged = copy.deepcopy(stored.signed)
    forged["mandate"]["budget_total"] = "100000.00"
    stored.signed = forged
    session.commit()
    record = pay(session, key, provider, mid, "10.00", "r-1")
    assert record.status == "denied"
    assert any("invalid" in reason for reason in record.reasons)


def test_expired_mandate_denies(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    later = datetime.now(UTC) + timedelta(days=3)
    record = pay(session, key, provider, mid, "10.00", "r-1", now=later)
    assert record.status == "denied"
    assert any("expired" in reason for reason in record.reasons)


def test_provider_failure_marks_failed_and_frees_budget(
    session: Session, key: Ed25519PrivateKey
) -> None:
    failing = FakeProvider(fail=True)
    mid = new_mandate(session, key)
    record = pay(session, key, failing, mid, "10.00", "r-1")
    assert record.status == "failed"
    assert service.spent_amount(session, mid) == Decimal("0")
    assert entry_types(session)[-1] == "payment_failed"


def test_capture_flow(session: Session, key: Ed25519PrivateKey, provider: FakeProvider) -> None:
    mid = new_mandate(session, key)
    pay(session, key, provider, mid, "10.00", "r-1")
    record = service.capture_payment(session, "r-1", provider)
    assert record.status == "executed"
    assert record.capture_id == "CAP-FAKE-r-1"
    assert entry_types(session)[-1] == "payment_executed"
    with pytest.raises(StateError):
        service.capture_payment(session, "r-1", provider)


def test_capture_requires_open_order(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    pay(session, key, provider, mid, "30.00", "r-1")
    with pytest.raises(StateError):
        service.capture_payment(session, "r-1", provider)


def test_reject_frees_reserved_budget(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    pay(session, key, provider, mid, "30.00", "r-1")
    assert service.spent_amount(session, mid) == Decimal("30.00")
    record = service.reject_payment(session, "r-1")
    assert record.status == "rejected"
    assert service.spent_amount(session, mid) == Decimal("0")


def test_approve_requires_pending_state(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    pay(session, key, provider, mid, "10.00", "r-1")
    with pytest.raises(StateError):
        service.approve_payment(session, "r-1", key.public_key(), provider)


def test_approval_after_revocation_is_rejected(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    pay(session, key, provider, mid, "30.00", "r-1")
    service.revoke_mandate(session, mid)
    record = service.approve_payment(session, "r-1", key.public_key(), provider)
    assert record.status == "rejected"
    assert provider.orders == []


def test_unknown_payment_raises(session: Session, provider: FakeProvider) -> None:
    with pytest.raises(NotFoundError):
        service.capture_payment(session, "nope", provider)


def test_reviewer_can_escalate(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "10.00", "r-1", [StubReviewer(Decision.ESCALATE)])
    assert record.status == "pending_approval"
    assert any("stub" in reason for reason in record.reasons)


def test_reviewer_can_deny(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "10.00", "r-1", [StubReviewer(Decision.DENY)])
    assert record.status == "denied"
    assert provider.orders == []


def test_reviewer_cannot_override_policy_denial(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "60.00", "r-1", [StubReviewer(Decision.APPROVE)])
    assert record.status == "denied"


def test_failing_reviewer_fails_safe(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "10.00", "r-1", [BrokenReviewer()])
    assert record.status == "pending_approval"
    assert provider.orders == []


def test_denial_reasons_do_not_include_the_policy_approval_note(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "10.00", "r-1", [StubReviewer(Decision.DENY)])
    assert record.status == "denied"
    assert "within mandate limits" not in record.reasons
    assert any("stub" in reason for reason in record.reasons)


def test_clean_approval_keeps_the_policy_note(
    session: Session, key: Ed25519PrivateKey, provider: FakeProvider
) -> None:
    mid = new_mandate(session, key)
    record = pay(session, key, provider, mid, "10.00", "r-1", [StubReviewer(Decision.APPROVE)])
    assert record.reasons == ["within mandate limits"]
