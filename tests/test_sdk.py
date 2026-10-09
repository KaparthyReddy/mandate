from collections.abc import Iterator
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from mandate.main import app
from mandate.sdk import MandateAPIError, MandateClient, MandateStatus


@pytest.fixture
def client() -> Iterator[MandateClient]:
    with TestClient(app) as http:
        yield MandateClient(http=http)


def new_mandate(client: MandateClient) -> MandateStatus:
    return client.create_mandate(
        purpose="Weekly groceries",
        agent_id="agent-1",
        budget_total="500",
        per_txn_cap="100",
        approval_threshold="20",
    )


def pay(client: MandateClient, mandate: MandateStatus, amount: str, **kwargs: str):  # type: ignore[no-untyped-def]
    return client.request_payment(
        mandate_id=mandate.mandate_id,
        agent_id=mandate.agent_id,
        amount=amount,
        merchant=kwargs.get("merchant", "BigMart"),
        category="groceries",
        description="test",
        request_id=kwargs.get("request_id"),
    )


def test_create_mandate_returns_status(client: MandateClient) -> None:
    mandate = new_mandate(client)
    assert mandate.mandate_id.startswith("m-")
    assert mandate.status == "active"
    assert mandate.spent == Decimal("0.00")
    assert mandate.remaining == Decimal("500.00")
    assert mandate.purpose == "Weekly groceries"


def test_approved_payment(client: MandateClient) -> None:
    result = pay(client, new_mandate(client), "10.00")
    assert result.approved
    assert not result.needs_human and not result.blocked
    assert result.amount == Decimal("10.00")
    assert result.order_id
    assert result.approval_url


def test_payment_above_threshold_needs_a_human(client: MandateClient) -> None:
    assert pay(client, new_mandate(client), "30.00").needs_human


def test_payment_over_cap_is_blocked(client: MandateClient) -> None:
    result = pay(client, new_mandate(client), "150.00")
    assert result.blocked
    assert result.reasons


def test_request_ids_are_generated_and_unique(client: MandateClient) -> None:
    mandate = new_mandate(client)
    assert pay(client, mandate, "5.00").request_id != pay(client, mandate, "5.00").request_id


def test_same_request_id_is_idempotent(client: MandateClient) -> None:
    mandate = new_mandate(client)
    first = pay(client, mandate, "5.00", request_id="sdk-fixed-1")
    second = pay(client, mandate, "5.00", request_id="sdk-fixed-1")
    assert first.order_id == second.order_id
    assert client.get_mandate(mandate.mandate_id).spent == Decimal("5.00")


def test_get_and_list_payments(client: MandateClient) -> None:
    mandate = new_mandate(client)
    result = pay(client, mandate, "5.00")
    assert client.get_payment(result.request_id).status == result.status
    assert [p.request_id for p in client.list_payments(mandate.mandate_id)] == [result.request_id]


def test_unknown_payment_raises_404(client: MandateClient) -> None:
    with pytest.raises(MandateAPIError) as error:
        client.get_payment("does-not-exist")
    assert error.value.status_code == 404


def test_invalid_amount_raises_422_naming_the_field(client: MandateClient) -> None:
    mandate = new_mandate(client)
    with pytest.raises(MandateAPIError) as error:
        pay(client, mandate, "not-a-number")
    assert error.value.status_code == 422
    assert "amount" in error.value.detail


def test_api_key_is_sent_as_bearer_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")
    MandateClient(api_key="secret", http=http).list_payments()
    assert seen == ["Bearer secret"]
