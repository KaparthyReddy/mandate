from fastapi.testclient import TestClient

from mandate.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_is_stored_once() -> None:
    event = {"id": "WH-TEST-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}
    with TestClient(app) as client:
        assert client.post("/webhooks/paypal", json=event).status_code == 200
        assert client.post("/webhooks/paypal", json=event).status_code == 200
        events = client.get("/webhooks/events").json()
    assert sum(1 for e in events if e["event_id"] == "WH-TEST-1") == 1


def test_webhook_without_id_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/webhooks/paypal", json={"event_type": "X"})
    assert response.status_code == 400
