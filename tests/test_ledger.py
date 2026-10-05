from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, update
from sqlalchemy.orm import Session

from mandate import ledger
from mandate.ledger import GENESIS_HASH
from mandate.models import Base, LedgerEntry


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_listeners() -> Iterator[None]:
    ledger.clear_listeners()
    yield
    ledger.clear_listeners()


def test_first_entry_links_to_genesis(session: Session) -> None:
    entry = ledger.append(session, "mandate_issued", {"id": "m-1"})
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_HASH
    assert len(entry.entry_hash) == 64


def test_entries_are_chained(session: Session) -> None:
    first = ledger.append(session, "a", {"n": 1})
    second = ledger.append(session, "b", {"n": 2})
    assert second.seq == 2
    assert second.prev_hash == first.entry_hash


def test_empty_ledger_is_valid(session: Session) -> None:
    result = ledger.verify_chain(session)
    assert result.valid
    assert result.checked == 0
    assert result.head_hash == GENESIS_HASH


def test_valid_chain_verifies(session: Session) -> None:
    for i in range(3):
        ledger.append(session, "a", {"n": i})
    result = ledger.verify_chain(session)
    assert result.valid
    assert result.checked == 3
    assert result.head_seq == 3


def test_payload_tampering_is_detected(session: Session) -> None:
    for i in range(3):
        ledger.append(session, "a", {"n": i})
    session.execute(update(LedgerEntry).where(LedgerEntry.seq == 2).values(payload={"n": 999}))
    session.commit()
    session.expire_all()
    result = ledger.verify_chain(session)
    assert not result.valid
    assert result.first_bad_seq == 2


def test_rewritten_hash_breaks_next_link(session: Session) -> None:
    for i in range(3):
        ledger.append(session, "a", {"n": i})
    entry = session.get(LedgerEntry, 2)
    assert entry is not None
    forged_payload = {"n": 999}
    forged_hash = ledger.compute_hash(
        seq=2,
        prev_hash=entry.prev_hash,
        entry_type=entry.entry_type,
        actor=entry.actor,
        payload=forged_payload,
        created_at=entry.created_at,
        schema_version=entry.schema_version,
    )
    session.execute(
        update(LedgerEntry)
        .where(LedgerEntry.seq == 2)
        .values(payload=forged_payload, entry_hash=forged_hash)
    )
    session.commit()
    session.expire_all()
    result = ledger.verify_chain(session)
    assert not result.valid
    assert result.first_bad_seq == 3


def test_deleted_entry_is_detected(session: Session) -> None:
    for i in range(3):
        ledger.append(session, "a", {"n": i})
    session.execute(delete(LedgerEntry).where(LedgerEntry.seq == 2))
    session.commit()
    session.expire_all()
    result = ledger.verify_chain(session)
    assert not result.valid
    assert result.first_bad_seq == 3


def test_decimal_and_datetime_payloads_verify_after_reload(session: Session) -> None:
    payload = {"amount": Decimal("12.50"), "at": datetime(2026, 10, 5, tzinfo=UTC)}
    ledger.append(session, "payment_requested", payload)
    session.expire_all()
    assert ledger.verify_chain(session).valid


def test_listener_receives_entries(session: Session) -> None:
    received: list[LedgerEntry] = []
    ledger.add_listener(received.append)
    ledger.append(session, "a", {"n": 1})
    assert len(received) == 1
    assert received[0].seq == 1


def test_failing_listener_does_not_break_append(session: Session) -> None:
    def boom(_: LedgerEntry) -> None:
        raise RuntimeError("boom")

    ledger.add_listener(boom)
    entry = ledger.append(session, "a", {"n": 1})
    assert entry.seq == 1
    assert ledger.verify_chain(session).valid


def test_get_head(session: Session) -> None:
    assert ledger.get_head(session) == (0, GENESIS_HASH)
    entry = ledger.append(session, "a", {"n": 1})
    assert ledger.get_head(session) == (1, entry.entry_hash)
