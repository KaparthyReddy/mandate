import uuid
from typing import Any

from fastapi.testclient import TestClient

from mandate.main import app

MANDATE: dict[str, Any] = {
    "issuer": "user-1",
    "agent_id": "agent-1",
    "budget_total": "500.00",
    "per_txn_cap": "100.00",
    "approval_threshold": "20.00",
}


def request_body(mandate_id: str, amount: str) -> dict[str, Any]:
    return {
        "request_id": f"r-{uuid.uuid4().hex[:10]}",
        "mandate_id": mandate_id,
        "agent_id": "agent-1",
        "amount": amount,
        "merchant": "BigMart",
        "category": "groceries",
    }


def test_dashboard_is_served_at_root() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Mandate" in response.text
    assert "/kill-switch" in response.text


def test_kill_switch_revokes_mandates_and_rejects_pending_payments() -> None:
    with TestClient(app) as client:
        mandate_id = client.post("/mandates", json=MANDATE).json()["mandate"]["mandate_id"]
        pending = client.post("/payments/request", json=request_body(mandate_id, "30.00")).json()
        assert pending["status"] == "pending_approval"
        result = client.post("/kill-switch").json()
        assert result["mandates_revoked"] >= 1
        assert result["payments_rejected"] >= 1
        assert client.get(f"/mandates/{mandate_id}").json()["status"] == "revoked"
        assert client.get(f"/payments/{pending['request_id']}").json()["status"] == "rejected"
        blocked = client.post("/payments/request", json=request_body(mandate_id, "5.00")).json()
        assert blocked["status"] == "denied"
        entries = client.get("/ledger", params={"limit": 500}).json()
        assert any(e["entry_type"] == "kill_switch" for e in entries)
        assert client.get("/ledger/verify").json()["valid"] is True


def test_kill_switch_with_nothing_active_is_harmless() -> None:
    with TestClient(app) as client:
        client.post("/kill-switch")
        result = client.post("/kill-switch").json()
    assert result == {"mandates_revoked": 0, "payments_rejected": 0}
