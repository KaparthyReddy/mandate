import uuid

from fastapi.testclient import TestClient

from mandate.main import app


def test_webhook_creates_ledger_entry_once() -> None:
    event_id = f"WH-{uuid.uuid4()}"
    event = {"id": event_id, "event_type": "PAYMENT.CAPTURE.COMPLETED"}
    with TestClient(app) as client:
        client.post("/webhooks/paypal", json=event)
        client.post("/webhooks/paypal", json=event)
        entries = client.get("/ledger", params={"limit": 500}).json()
    matches = [e for e in entries if e["payload"].get("event_id") == event_id]
    assert len(matches) == 1
    assert matches[0]["entry_type"] == "webhook_received"
    assert matches[0]["actor"] == "paypal"


def test_verify_endpoint_reports_valid_chain() -> None:
    with TestClient(app) as client:
        client.post("/webhooks/paypal", json={"id": f"WH-{uuid.uuid4()}", "event_type": "X"})
        result = client.get("/ledger/verify").json()
    assert result["valid"] is True
    assert result["checked"] >= 1


def test_ledger_pagination() -> None:
    with TestClient(app) as client:
        for _ in range(3):
            client.post("/webhooks/paypal", json={"id": f"WH-{uuid.uuid4()}", "event_type": "X"})
        first = client.get("/ledger", params={"limit": 1}).json()
        rest = client.get("/ledger", params={"after_seq": first[0]["seq"], "limit": 1}).json()
    assert rest[0]["seq"] == first[0]["seq"] + 1
