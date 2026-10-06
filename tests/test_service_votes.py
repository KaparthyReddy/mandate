from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mandate import crypto, service
from mandate.council import Council
from mandate.intent_agent import IntentAgent
from mandate.llm import FakeLLM
from mandate.models import Base, LedgerEntry
from mandate.policy import PaymentRequest
from mandate.provider import FakeProvider
from mandate.service import MandateCreate


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'votes.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def run_payment(session: Session, llm_response: str, amount: str = "10.00") -> tuple[str, str]:
    key = crypto.generate_private_key()
    provider = FakeProvider()
    create = MandateCreate(
        issuer="user-1",
        agent_id="agent-1",
        purpose="Weekly groceries",
        budget_total=Decimal("100"),
        per_txn_cap=Decimal("50"),
        approval_threshold=Decimal("20"),
    )
    mandate_id = service.create_mandate(session, create, key).mandate.mandate_id
    request = PaymentRequest(
        request_id="r-1",
        mandate_id=mandate_id,
        agent_id="agent-1",
        amount=Decimal(amount),
        merchant="GameZone",
        category="electronics",
        description="Gaming headset",
    )
    council = Council([IntentAgent(FakeLLM(llm_response))])
    record = service.process_payment(session, request, key.public_key(), provider, [council])
    return record.status, record.mandate_id


def test_council_denial_blocks_payment_and_logs_votes(session: Session) -> None:
    raw = '{"verdict": "mismatch", "confidence": 0.95, "reason": "not groceries"}'
    status, _ = run_payment(session, raw)
    assert status == "denied"
    entry = session.scalars(
        select(LedgerEntry).where(LedgerEntry.entry_type == "decision_made")
    ).one()
    votes = entry.payload["votes"]
    assert votes[0]["reviewer"] == "council"
    assert votes[0]["details"]["votes"][0]["reviewer"] == "intent"
    assert votes[0]["details"]["votes"][0]["details"]["verdict"] == "mismatch"


def test_council_match_allows_payment(session: Session) -> None:
    raw = '{"verdict": "match", "confidence": 0.95, "reason": "fine"}'
    status, _ = run_payment(session, raw)
    assert status == "order_created"


def test_council_cannot_rescue_policy_denial(session: Session) -> None:
    raw = '{"verdict": "match", "confidence": 1.0, "reason": "fine"}'
    status, _ = run_payment(session, raw, amount="60.00")
    assert status == "denied"


def test_garbage_llm_output_escalates_to_human(session: Session) -> None:
    status, _ = run_payment(session, "not json at all")
    assert status == "pending_approval"
