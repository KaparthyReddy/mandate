from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from mandate import crypto
from mandate.mandates import (
    ExpiredError,
    InvalidSignatureError,
    Mandate,
    NotYetValidError,
    SignedMandate,
    sign_mandate,
    verify_mandate,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def make_mandate(**overrides: Any) -> Mandate:
    data: dict[str, Any] = {
        "mandate_id": "m-1",
        "issuer": "user-1",
        "agent_id": "agent-1",
        "currency": "USD",
        "budget_total": Decimal("100.00"),
        "per_txn_cap": Decimal("50.00"),
        "approval_threshold": Decimal("20.00"),
        "allowed_categories": ["groceries"],
        "allowed_merchants": [],
        "issued_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=1),
    }
    data.update(overrides)
    return Mandate(**data)


def test_sign_and_verify() -> None:
    key = crypto.generate_private_key()
    signed = sign_mandate(make_mandate(), key)
    assert verify_mandate(signed, key.public_key(), NOW).mandate_id == "m-1"


def test_tampered_mandate_is_rejected() -> None:
    key = crypto.generate_private_key()
    signed = sign_mandate(make_mandate(), key)
    tampered = SignedMandate(
        mandate=make_mandate(budget_total=Decimal("100000.00"), per_txn_cap=Decimal("50.00")),
        signature=signed.signature,
    )
    with pytest.raises(InvalidSignatureError):
        verify_mandate(tampered, key.public_key(), NOW)


def test_wrong_key_is_rejected() -> None:
    signed = sign_mandate(make_mandate(), crypto.generate_private_key())
    other = crypto.generate_private_key().public_key()
    with pytest.raises(InvalidSignatureError):
        verify_mandate(signed, other, NOW)


def test_garbage_signature_is_rejected() -> None:
    key = crypto.generate_private_key()
    signed = SignedMandate(mandate=make_mandate(), signature="not-base64!!")
    with pytest.raises(InvalidSignatureError):
        verify_mandate(signed, key.public_key(), NOW)


def test_expired_mandate_is_rejected() -> None:
    key = crypto.generate_private_key()
    signed = sign_mandate(make_mandate(), key)
    with pytest.raises(ExpiredError):
        verify_mandate(signed, key.public_key(), NOW + timedelta(days=2))


def test_not_yet_valid_mandate_is_rejected() -> None:
    key = crypto.generate_private_key()
    signed = sign_mandate(make_mandate(), key)
    with pytest.raises(NotYetValidError):
        verify_mandate(signed, key.public_key(), NOW - timedelta(days=1))


def test_json_round_trip_still_verifies() -> None:
    key = crypto.generate_private_key()
    signed = sign_mandate(make_mandate(), key)
    restored = SignedMandate.model_validate_json(signed.model_dump_json())
    assert verify_mandate(restored, key.public_key(), NOW).agent_id == "agent-1"


def test_key_base64_round_trip() -> None:
    key = crypto.generate_private_key()
    restored = crypto.private_key_from_b64(crypto.private_key_to_b64(key))
    public = crypto.public_key_from_b64(crypto.public_key_to_b64(key.public_key()))
    signed = sign_mandate(make_mandate(), restored)
    assert verify_mandate(signed, public, NOW).mandate_id == "m-1"


def test_inconsistent_mandate_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_mandate(per_txn_cap=Decimal("500.00"))
    with pytest.raises(ValueError):
        make_mandate(expires_at=NOW - timedelta(days=2))
