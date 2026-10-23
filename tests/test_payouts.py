import asyncio
import copy
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mandate import crypto, service
from mandate.anomaly import PaymentSnapshot
from mandate.bench.generate import generate
from mandate.bench.runner import run_config, vendor_list
from mandate.embeddings import HashingEmbedder
from mandate.main import app
from mandate.mandates import Mandate
from mandate.mcp_server import build_server
from mandate.models import Base, LedgerEntry, MandateRecord
from mandate.policy import Decision, PaymentRequest, evaluate
from mandate.provider import FakeProvider, PayPalProvider, ProviderError
from mandate.reputation import ReputationIndex, load_records
from mandate.risk_agent import RiskAgent
from mandate.sdk import MandateClient
from mandate.service import MandateCreate, StateError

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
VENDOR = "vendor@example.com"


def make_mandate(**overrides: Any) -> Mandate:
    data: dict[str, Any] = {
        "mandate_id": "m-1",
        "issuer": "u",
        "agent_id": "a",
        "budget_total": Decimal("500"),
        "per_txn_cap": Decimal("100"),
        "approval_threshold": Decimal("50"),
        "issued_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
    }
    data.update(overrides)
    return Mandate(**data)


def make_request(**overrides: Any) -> PaymentRequest:
    data: dict[str, Any] = {
        "request_id": "r-1",
        "mandate_id": "m-1",
        "agent_id": "a",
        "amount": Decimal("20"),
        "merchant": "Berliner Taxi",
        "category": "transport",
    }
    data.update(overrides)
    return PaymentRequest(**data)


def decide(mandate: Mandate, request: PaymentRequest) -> Decision:
    return evaluate(mandate, request, Decimal("0"), NOW).decision


def test_payouts_are_denied_when_the_mandate_lists_no_recipients() -> None:
    result = evaluate(make_mandate(), make_request(recipient=VENDOR), Decimal("0"), NOW)
    assert result.decision == Decision.DENY
    assert any("does not allow direct payouts" in reason for reason in result.reasons)


def test_payouts_to_unlisted_recipients_are_denied() -> None:
    mandate = make_mandate(allowed_recipients=[VENDOR])
    assert decide(mandate, make_request(recipient="attacker@evil.example")) == Decision.DENY


def test_payouts_to_listed_recipients_pass_case_insensitively() -> None:
    mandate = make_mandate(allowed_recipients=[VENDOR])
    assert decide(mandate, make_request(recipient="Vendor@Example.com")) == Decision.APPROVE


def test_a_request_without_a_recipient_ignores_the_recipient_list() -> None:
    assert decide(make_mandate(allowed_recipients=[VENDOR]), make_request()) == Decision.APPROVE


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'payouts.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture
def key() -> Any:
    return crypto.generate_private_key()


def issue(session: Session, key: Any, **overrides: Any) -> str:
    data: dict[str, Any] = {
        "issuer": "u",
        "agent_id": "agent-1",
        "purpose": "Pay contractors",
        "budget_total": Decimal("500"),
        "per_txn_cap": Decimal("100"),
        "approval_threshold": Decimal("50"),
        "allowed_recipients": [VENDOR],
    }
    data.update(overrides)
    return service.create_mandate(session, MandateCreate(**data), key).mandate.mandate_id


def payout(
    session: Session,
    key: Any,
    provider: FakeProvider,
    mandate_id: str,
    amount: str,
    rid: str,
    recipient: str = VENDOR,
) -> Any:
    request = PaymentRequest(
        request_id=rid,
        mandate_id=mandate_id,
        agent_id="agent-1",
        amount=Decimal(amount),
        merchant="Berliner Taxi",
        category="transport",
        recipient=recipient,
    )
    return service.process_payment(session, request, key.public_key(), provider)


def test_listed_payout_is_sent_immediately_and_logged_with_a_masked_recipient(
    session: Session, key: Any
) -> None:
    provider = FakeProvider()
    record = payout(session, key, provider, issue(session, key), "20.00", "p-1")
    assert record.status == "executed"
    assert record.provider_order_id == "PAYOUT-p-1"
    assert provider.payouts == [("PAYOUT-p-1", VENDOR)]
    entry = session.scalars(
        select(LedgerEntry).where(LedgerEntry.entry_type == "payment_executed")
    ).one()
    assert entry.payload["kind"] == "payout"
    assert entry.payload["recipient"] == "v***@example.com"
    assert VENDOR not in str(entry.payload)


def test_unlisted_recipient_is_denied_and_the_provider_is_never_called(
    session: Session, key: Any
) -> None:
    provider = FakeProvider()
    record = payout(
        session, key, provider, issue(session, key), "20.00", "p-1", "attacker@evil.example"
    )
    assert record.status == "denied"
    assert provider.payouts == []


def test_large_payout_waits_for_a_human_then_goes_out(session: Session, key: Any) -> None:
    provider = FakeProvider()
    mandate_id = issue(session, key)
    record = payout(session, key, provider, mandate_id, "75.00", "p-1")
    assert record.status == "pending_approval"
    assert provider.payouts == []
    approved = service.approve_payment(session, "p-1", key.public_key(), provider)
    assert approved.status == "executed"
    assert len(provider.payouts) == 1


def test_failed_payout_frees_the_budget(session: Session, key: Any) -> None:
    mandate_id = issue(session, key)
    record = payout(session, key, FakeProvider(fail=True), mandate_id, "20.00", "p-1")
    assert record.status == "failed"
    assert service.spent_amount(session, mandate_id) == Decimal("0")


def test_a_payout_cannot_be_captured(session: Session, key: Any) -> None:
    provider = FakeProvider()
    payout(session, key, provider, issue(session, key), "20.00", "p-1")
    with pytest.raises(StateError):
        service.capture_payment(session, "p-1", provider)


def test_adding_a_recipient_to_a_stored_mandate_breaks_its_signature(
    session: Session, key: Any
) -> None:
    mandate_id = issue(session, key)
    stored = session.get(MandateRecord, mandate_id)
    assert stored is not None
    forged = copy.deepcopy(stored.signed)
    forged["mandate"]["allowed_recipients"].append("attacker@evil.example")
    stored.signed = forged
    session.commit()
    record = payout(
        session, key, FakeProvider(), mandate_id, "20.00", "p-1", "attacker@evil.example"
    )
    assert record.status == "denied"
    assert any("invalid" in reason for reason in record.reasons)


@pytest.fixture
def client() -> Iterator[MandateClient]:
    with TestClient(app) as http:
        yield MandateClient(http=http)


def test_sdk_creates_payout_mandates_and_pays_listed_vendors(client: MandateClient) -> None:
    mandate = client.create_mandate(
        purpose="Pay contractors",
        agent_id="agent-1",
        budget_total="500",
        per_txn_cap="100",
        approval_threshold="50",
        allowed_recipients=[VENDOR],
        new_payee_approval=True,
    )
    assert mandate.allowed_recipients == [VENDOR]
    assert mandate.new_payee_approval is True
    result = client.request_payment(
        mandate_id=mandate.mandate_id,
        agent_id="agent-1",
        amount="20.00",
        merchant="Berliner Taxi",
        category="transport",
        recipient=VENDOR,
    )
    assert result.approved
    assert result.recipient == VENDOR
    assert result.order_id and result.order_id.startswith("PAYOUT-")
    assert result.approval_url is None
    other = client.request_payment(
        mandate_id=mandate.mandate_id,
        agent_id="agent-1",
        amount="20.00",
        merchant="Berliner Taxi",
        category="transport",
        recipient="attacker@evil.example",
    )
    assert other.blocked


def test_mcp_tool_pays_a_listed_vendor_directly(client: MandateClient) -> None:
    mandate = client.create_mandate(
        purpose="Pay contractors",
        agent_id="agent-1",
        budget_total="500",
        per_txn_cap="100",
        approval_threshold="50",
        allowed_recipients=[VENDOR],
    )
    server = build_server(client, mandate.mandate_id)
    arguments = {
        "amount": "20.00",
        "merchant": "Berliner Taxi",
        "category": "transport",
        "recipient": VENDOR,
    }
    result: Any = asyncio.run(server.call_tool("request_payment", arguments))
    assert result.structured_content is not None
    assert result.structured_content["approved"] is True
    assert "Payout sent" in result.structured_content["message"]


def test_dashboard_form_offers_the_new_controls(client: MandateClient) -> None:
    with TestClient(app) as http:
        page = http.get("/").text
    assert 'name="recipients"' in page
    assert 'name="new_payee"' in page


class FakeResponse:
    def __init__(self, body: dict[str, Any], ok: bool = True, status: int = 200) -> None:
        self._body = body
        self.ok = ok
        self.status_code = status
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


def test_paypal_provider_builds_a_payouts_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        if url.endswith("/v1/oauth2/token"):
            return FakeResponse({"access_token": "tok", "expires_in": 300})
        return FakeResponse(
            {"batch_header": {"payout_batch_id": "BATCH1", "batch_status": "PENDING"}}
        )

    monkeypatch.setattr(requests, "post", fake_post)
    provider = PayPalProvider("https://api-m.sandbox.paypal.com/", "id", "secret")
    result = provider.create_payout(
        make_request(amount=Decimal("12.5"), recipient=VENDOR, description="Taxi")
    )
    assert (result.payout_id, result.status) == ("BATCH1", "PENDING")
    payout_call = calls[-1]
    assert payout_call["url"] == "https://api-m.sandbox.paypal.com/v1/payments/payouts"
    assert payout_call["headers"]["PayPal-Request-Id"] == "payout-r-1"
    body = payout_call["json"]
    item = body["items"][0]
    assert item["receiver"] == VENDOR
    assert item["recipient_type"] == "EMAIL"
    assert item["amount"] == {"value": "12.50", "currency": "USD"}
    assert len(body["sender_batch_header"]["sender_batch_id"]) == 30


def test_paypal_payout_errors_become_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith("/v1/oauth2/token"):
            return FakeResponse({"access_token": "tok", "expires_in": 300})
        return FakeResponse({"name": "AUTHORIZATION_ERROR"}, ok=False, status=403)

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(ProviderError, match="403"):
        PayPalProvider("https://x", "id", "secret").create_payout(make_request(recipient=VENDOR))


class StaticHistory:
    def __init__(self, items: list[PaymentSnapshot] | None = None) -> None:
        self.items = items or []

    def recent_payments(self, mandate_id: str, since: datetime) -> list[PaymentSnapshot]:
        return self.items


def risk_vote(merchant: str, strict: bool, history: list[PaymentSnapshot] | None = None) -> Any:
    index = ReputationIndex(load_records(), HashingEmbedder(), use_faiss=False)
    agent = RiskAgent(index, StaticHistory(history), clock=lambda: NOW)
    mandate = make_mandate(new_payee_approval=strict)
    return agent.review(mandate, make_request(merchant=merchant), Decimal("0"))


def paid(merchant: str, status: str = "executed") -> PaymentSnapshot:
    return PaymentSnapshot(Decimal("20"), merchant, NOW - timedelta(days=1), status)


def test_new_payee_approval_holds_the_first_payment_to_an_unlisted_merchant() -> None:
    vote = risk_vote("Maple Street Bakery", strict=True)
    assert vote.decision == Decision.ESCALATE
    assert any("first payment" in reason for reason in vote.reasons)


def test_new_payee_approval_is_lifted_after_a_successful_payment() -> None:
    assert (
        risk_vote("Maple Street Bakery", True, [paid("maple street bakery")]).decision
        == Decision.APPROVE
    )


def test_denied_history_does_not_make_a_payee_known() -> None:
    assert (
        risk_vote("Maple Street Bakery", True, [paid("Maple Street Bakery", "denied")]).decision
        == Decision.ESCALATE
    )


def test_trusted_brands_are_exempt_from_new_payee_approval() -> None:
    assert risk_vote("Staples", strict=True).decision == Decision.APPROVE


def test_new_payee_approval_is_off_by_default() -> None:
    assert risk_vote("Maple Street Bakery", strict=False).decision == Decision.APPROVE


def test_vendor_list_covers_each_purpose_and_ignores_unknown_ones() -> None:
    vendors = vendor_list("Weekly groceries for a family of four")
    assert "BigMart" in vendors and "Maple Street Bakery" in vendors
    assert vendors == sorted(set(vendors))
    assert vendor_list("something else") == []


def test_an_approved_vendor_list_stops_redirects_typosquats_and_unrelated_purchases(
    tmp_path: Path,
) -> None:
    cases = generate(7, "dev", 6, 0.3)
    results = run_config("limits_allowlist", cases, None, tmp_path)
    for name in ("redirect", "lookalike", "unrelated", "probing", "override"):
        rows = [r for r in results if r.cls == name]
        assert rows and not any(r.attack_succeeded for r in rows), name
    assert all(r.blocked == 0 for r in results if r.kind == "honest")


def test_strict_mode_holds_small_vendor_purchases_but_not_trusted_ones(tmp_path: Path) -> None:
    cases = generate(7, "dev", 3, 0.3)
    results = {r.case_id: r for r in run_config("rules_strict", cases, None, tmp_path)}
    trusted = {r.name for r in load_records() if r.reputation == "trusted"}
    small = [results[c.id] for c in cases if c.cls == "small_vendor"]
    routine = [
        results[c.id] for c in cases if c.cls == "routine" and c.requests[0].merchant in trusted
    ]
    assert small and all(r.escalated == 1 for r in small)
    assert routine and all(r.paid == 1 for r in routine)
