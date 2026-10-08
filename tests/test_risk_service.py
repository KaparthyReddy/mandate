from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mandate import crypto, service
from mandate.council import Council
from mandate.embeddings import HashingEmbedder
from mandate.models import Base
from mandate.policy import PaymentRequest
from mandate.provider import FakeProvider
from mandate.reputation import ReputationIndex, load_records
from mandate.risk_agent import RiskAgent
from mandate.risk_history import DbHistoryProvider
from mandate.service import MandateCreate


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'risk.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def run(factory: sessionmaker[Session], merchant: str, category: str, request_id: str) -> str:
    key = crypto.generate_private_key()
    provider = FakeProvider()
    index = ReputationIndex(load_records(), HashingEmbedder(), use_faiss=False)
    council = Council([RiskAgent(index, DbHistoryProvider(factory))])
    with factory() as session:
        create = MandateCreate(
            issuer="user-1",
            agent_id="agent-1",
            budget_total=Decimal("1000"),
            per_txn_cap=Decimal("100"),
            approval_threshold=Decimal("50"),
        )
        mandate_id = service.create_mandate(session, create, key).mandate.mandate_id
        request = PaymentRequest(
            request_id=request_id,
            mandate_id=mandate_id,
            agent_id="agent-1",
            amount=Decimal("10"),
            merchant=merchant,
            category=category,
        )
        record = service.process_payment(session, request, key.public_key(), provider, [council])
        return record.status


def test_blocked_merchant_is_denied_through_the_pipeline(factory: sessionmaker[Session]) -> None:
    assert run(factory, "amaz0n-deals", "retail", "r-1") == "denied"


def test_trusted_merchant_passes_through_the_pipeline(factory: sessionmaker[Session]) -> None:
    assert run(factory, "Staples", "office_supplies", "r-2") == "order_created"


def test_risky_merchant_goes_to_a_human(factory: sessionmaker[Session]) -> None:
    assert run(factory, "CryptoExchange", "finance", "r-3") == "pending_approval"
