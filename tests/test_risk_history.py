from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mandate.models import Base, PaymentRecord
from mandate.risk_history import DbHistoryProvider

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def add(
    session: Session, request_id: str, mandate_id: str, hours_ago: int, amount: str = "10.00"
) -> None:
    stamp = (NOW - timedelta(hours=hours_ago)).isoformat()
    session.add(
        PaymentRecord(
            request_id=request_id,
            mandate_id=mandate_id,
            agent_id="agent-1",
            amount=amount,
            currency="USD",
            merchant="BigMart",
            category="groceries",
            description="",
            status="executed",
            decision="approve",
            reasons=[],
            created_at=stamp,
            updated_at=stamp,
        )
    )


def test_returns_only_this_mandates_recent_payments(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        add(session, "r-1", "m-1", hours_ago=1)
        add(session, "r-2", "m-1", hours_ago=24 * 10)
        add(session, "r-3", "m-2", hours_ago=1, amount="99.00")
        session.commit()
    provider = DbHistoryProvider(factory)
    items = provider.recent_payments("m-1", NOW - timedelta(days=7))
    assert len(items) == 1
    assert items[0].amount == Decimal("10.00")
    assert items[0].merchant == "BigMart"
    assert items[0].created_at.tzinfo is not None


def test_unknown_mandate_has_no_history(factory: sessionmaker[Session]) -> None:
    assert DbHistoryProvider(factory).recent_payments("nope", NOW - timedelta(days=7)) == []
