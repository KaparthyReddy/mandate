from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mandate.anomaly import PaymentSnapshot, detect
from mandate.mandates import Mandate
from mandate.policy import PaymentRequest

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def make_mandate() -> Mandate:
    return Mandate(
        mandate_id="m-1",
        issuer="user-1",
        agent_id="agent-1",
        budget_total=Decimal("1000"),
        per_txn_cap=Decimal("100"),
        approval_threshold=Decimal("50"),
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
    )


def make_request(amount: str = "10", merchant: str = "BigMart") -> PaymentRequest:
    return PaymentRequest(
        request_id="r-1",
        mandate_id="m-1",
        agent_id="agent-1",
        amount=Decimal(amount),
        merchant=merchant,
        category="groceries",
    )


def snap(
    amount: str = "10", merchant: str = "BigMart", minutes_ago: int = 60, status: str = "executed"
) -> PaymentSnapshot:
    return PaymentSnapshot(Decimal(amount), merchant, NOW - timedelta(minutes=minutes_ago), status)


def names(history: list[PaymentSnapshot], request: PaymentRequest, spent: str = "0") -> set[str]:
    return {s.name for s in detect(make_mandate(), request, Decimal(spent), history, NOW)}


def test_clean_baseline_has_no_signals() -> None:
    assert names([snap()], make_request()) == set()


def test_first_ever_payment_is_not_a_new_merchant() -> None:
    assert "new_merchant" not in names([], make_request())


def test_new_merchant_is_flagged() -> None:
    assert "new_merchant" in names([snap(merchant="FreshFarm")], make_request())


def test_new_merchant_check_ignores_case() -> None:
    assert "new_merchant" not in names([snap(merchant="bigmart")], make_request())


def test_amount_outlier_needs_enough_history() -> None:
    few = [snap("10") for _ in range(4)]
    assert "amount_outlier" not in names(few, make_request("90"))


def test_amount_outlier_is_flagged() -> None:
    history = [snap("10", minutes_ago=100 + i) for i in range(6)]
    assert "amount_outlier" in names(history, make_request("90"))


def test_denied_payments_do_not_shape_the_baseline() -> None:
    history = [snap("10", minutes_ago=100 + i) for i in range(5)] + [
        snap("500", minutes_ago=200 + i, status="denied") for i in range(10)
    ]
    assert "amount_outlier" in names(history, make_request("90"))


def test_near_threshold_is_flagged() -> None:
    assert "near_limit" in names([snap()], make_request("48"))
    assert "near_limit" in names([snap()], make_request("50"))
    assert "near_limit" not in names([snap()], make_request("40"))


def test_near_cap_is_flagged() -> None:
    assert "near_limit" in names([snap()], make_request("95"))


def test_velocity_thresholds() -> None:
    five = [snap(minutes_ago=2) for _ in range(5)]
    ten = [snap(minutes_ago=2) for _ in range(10)]
    slow = [snap(minutes_ago=30) for _ in range(10)]
    signals = {
        s.name: s.score for s in detect(make_mandate(), make_request(), Decimal("0"), five, NOW)
    }
    assert signals["velocity"] == 0.30
    signals = {
        s.name: s.score for s in detect(make_mandate(), make_request(), Decimal("0"), ten, NOW)
    }
    assert signals["velocity"] == 0.50
    assert "velocity" not in names(slow, make_request())


def test_velocity_ignores_denied_requests() -> None:
    denied = [snap(minutes_ago=2, status="denied") for _ in range(5)]
    assert "velocity" not in names(denied, make_request())


def test_repeated_denials_are_flagged_as_probing() -> None:
    three = [snap(minutes_ago=2, status="denied") for _ in range(3)]
    six = [snap(minutes_ago=2, status="denied") for _ in range(6)]
    two = [snap(minutes_ago=2, status="denied") for _ in range(2)]
    scores = {
        s.name: s.score for s in detect(make_mandate(), make_request(), Decimal("0"), three, NOW)
    }
    assert scores["repeated_denials"] == 0.30
    scores = {
        s.name: s.score for s in detect(make_mandate(), make_request(), Decimal("0"), six, NOW)
    }
    assert scores["repeated_denials"] == 0.50
    assert "repeated_denials" not in names(two, make_request())


def test_old_denials_do_not_count() -> None:
    old = [snap(minutes_ago=60, status="denied") for _ in range(6)]
    assert "repeated_denials" not in names(old, make_request())


def test_repeated_amount_is_flagged() -> None:
    history = [snap("12.00", minutes_ago=10 * i + 5) for i in range(3)]
    assert "repeated_amount" in names(history, make_request("12.00"))
    assert "repeated_amount" not in names(history, make_request("13.00"))


def test_budget_burn_is_flagged() -> None:
    assert "budget_burn" in names([snap()], make_request("10"), spent="800")
    assert "budget_burn" not in names([snap()], make_request("10"), spent="100")
