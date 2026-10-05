from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest, evaluate

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
        "allowed_merchants": ["BigMart"],
        "issued_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=1),
    }
    data.update(overrides)
    return Mandate(**data)


def make_request(**overrides: Any) -> PaymentRequest:
    data: dict[str, Any] = {
        "request_id": "r-1",
        "mandate_id": "m-1",
        "agent_id": "agent-1",
        "amount": Decimal("10.00"),
        "currency": "USD",
        "merchant": "bigmart",
        "category": "Groceries",
    }
    data.update(overrides)
    return PaymentRequest(**data)


def run(request: PaymentRequest, mandate: Mandate | None = None, spent: str = "0") -> Decision:
    return evaluate(mandate or make_mandate(), request, Decimal(spent), NOW).decision


def test_approve_within_limits() -> None:
    assert run(make_request()) == Decision.APPROVE


def test_escalate_above_threshold() -> None:
    assert run(make_request(amount=Decimal("30.00"))) == Decision.ESCALATE


def test_deny_above_per_txn_cap() -> None:
    assert run(make_request(amount=Decimal("60.00"))) == Decision.DENY


def test_deny_when_budget_exhausted() -> None:
    assert run(make_request(amount=Decimal("10.00")), spent="95.00") == Decision.DENY


def test_approve_exactly_at_remaining_budget() -> None:
    assert run(make_request(amount=Decimal("10.00")), spent="90.00") == Decision.APPROVE


def test_deny_unlisted_merchant() -> None:
    assert run(make_request(merchant="ShadyShop")) == Decision.DENY


def test_deny_wrong_category() -> None:
    assert run(make_request(category="electronics")) == Decision.DENY


def test_deny_wrong_currency() -> None:
    assert run(make_request(currency="EUR")) == Decision.DENY


def test_deny_wrong_agent() -> None:
    assert run(make_request(agent_id="agent-evil")) == Decision.DENY


def test_deny_wrong_mandate() -> None:
    assert run(make_request(mandate_id="m-2")) == Decision.DENY


def test_deny_after_expiry() -> None:
    mandate = make_mandate(expires_at=NOW - timedelta(minutes=1), issued_at=NOW - timedelta(days=1))
    assert run(make_request(), mandate) == Decision.DENY


def test_empty_allowlists_mean_unrestricted() -> None:
    mandate = make_mandate(allowed_merchants=[], allowed_categories=[])
    assert run(make_request(merchant="Anything", category="anything"), mandate) == Decision.APPROVE


def test_multiple_violations_are_all_reported() -> None:
    result = evaluate(
        make_mandate(),
        make_request(amount=Decimal("60.00"), merchant="ShadyShop"),
        Decimal("0"),
        NOW,
    )
    assert result.decision == Decision.DENY
    assert len(result.reasons) >= 2
