import uuid
from typing import Any

from fastapi.testclient import TestClient

from mandate.main import app

MANDATE_BODY: dict[str, Any] = {
    "issuer": "user-1",
    "agent_id": "agent-1",
    "budget_total": "100.00",
    "per_txn_cap": "50.00",
    "approval_threshold": "20.00",
    "allowed_categories": ["groceries"],
}


def new_mandate(client: TestClient) -> str:
    response = client.post("/mandates", json=MANDATE_BODY)
    assert response.status_code == 200, response.text
    return str(response.json()["mandate"]["mandate_id"])


def pay(client: TestClient, mandate_id: str, amount: str) -> dict[str, Any]:
    body = {
        "request_id": f"r-{uuid.uuid4().hex[:10]}",
        "mandate_id": mandate_id,
        "agent_id": "agent-1",
        "amount": amount,
        "merchant": "BigMart",
        "category": "groceries",
    }
    response = client.post("/payments/request", json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_create_and_view_mandate() -> None:
    with TestClient(app) as client:
        mid = new_mandate(client)
        view = client.get(f"/mandates/{mid}").json()
    assert view["status"] == "active"
    assert view["spent"] == "0.00"
    assert view["remaining"] == "100.00"


def test_invalid_mandate_is_rejected() -> None:
    body = {**MANDATE_BODY, "per_txn_cap": "500.00"}
    with TestClient(app) as client:
        response = client.post("/mandates", json=body)
    assert response.status_code == 422


def test_approved_payment_updates_spend() -> None:
    with TestClient(app) as client:
        mid = new_mandate(client)
        payment = pay(client, mid, "5.00")
        view = client.get(f"/mandates/{mid}").json()
    assert payment["status"] == "order_created"
    assert payment["approval_url"]
    assert view["spent"] == "5.00"
    assert view["remaining"] == "95.00"


def test_escalate_approve_capture_flow() -> None:
    with TestClient(app) as client:
        mid = new_mandate(client)
        payment = pay(client, mid, "30.00")
        assert payment["status"] == "pending_approval"
        rid = payment["request_id"]
        approved = client.post(f"/payments/{rid}/approve").json()
        assert approved["status"] == "order_created"
        captured = client.post(f"/payments/{rid}/capture").json()
        assert captured["status"] == "executed"
        verify = client.get("/ledger/verify").json()
    assert verify["valid"] is True


def test_over_cap_payment_is_denied() -> None:
    with TestClient(app) as client:
        mid = new_mandate(client)
        payment = pay(client, mid, "60.00")
    assert payment["status"] == "denied"


def test_revoked_mandate_blocks_payments() -> None:
    with TestClient(app) as client:
        mid = new_mandate(client)
        assert client.post(f"/mandates/{mid}/revoke").json()["status"] == "revoked"
        payment = pay(client, mid, "5.00")
    assert payment["status"] == "denied"


def test_capture_without_approval_conflicts() -> None:
    with TestClient(app) as client:
        mid = new_mandate(client)
        rid = pay(client, mid, "30.00")["request_id"]
        response = client.post(f"/payments/{rid}/capture")
    assert response.status_code == 409


def test_unknown_payment_is_404() -> None:
    with TestClient(app) as client:
        response = client.get("/payments/does-not-exist")
    assert response.status_code == 404
