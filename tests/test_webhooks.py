import dataclasses
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from test_wiring import make_settings

from mandate.main import app
from mandate.provider import PayPalProvider
from mandate.webhooks import (
    DenyAllVerifier,
    FakeVerifier,
    NoopVerifier,
    PayPalWebhookVerifier,
    build_webhook_verifier,
    get_webhook_verifier,
)

HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "paypal-transmission-id": "tx-1",
    "paypal-transmission-sig": "sig",
    "paypal-transmission-time": "2026-10-10T12:00:00Z",
}
EVENT = {"id": "WH-SIGNED-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}


@pytest.fixture
def client_with() -> Iterator[Any]:
    def build(verifier: FakeVerifier) -> TestClient:
        app.dependency_overrides[get_webhook_verifier] = lambda: verifier
        return TestClient(app)

    try:
        yield build
    finally:
        app.dependency_overrides.clear()


def test_unverified_webhooks_are_rejected_and_not_stored(client_with: Any) -> None:
    verifier = FakeVerifier(accept=False)
    with client_with(verifier) as http:
        response = http.post("/webhooks/paypal", json=EVENT)
        stored = http.get("/webhooks/events").json()
    assert response.status_code == 401
    assert verifier.calls == 1
    assert all(item["event_id"] != "WH-SIGNED-1" for item in stored)


def test_verified_webhooks_are_stored(client_with: Any) -> None:
    event = {**EVENT, "id": "WH-SIGNED-2"}
    with client_with(FakeVerifier(accept=True)) as http:
        assert http.post("/webhooks/paypal", json=event).status_code == 200
        stored = http.get("/webhooks/events").json()
    assert any(item["event_id"] == "WH-SIGNED-2" for item in stored)


class Reply:
    def __init__(self, body: dict[str, Any], ok: bool = True) -> None:
        self._body = body
        self.ok = ok

    def json(self) -> dict[str, Any]:
        return self._body


def paypal_verifier() -> PayPalWebhookVerifier:
    return PayPalWebhookVerifier(
        PayPalProvider("https://api-m.sandbox.paypal.com", "id", "secret"), "WEBHOOK-ID"
    )


def install_post(
    monkeypatch: pytest.MonkeyPatch, status: str, ok: bool = True
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> Reply:
        calls.append({"url": url, **kwargs})
        if url.endswith("/v1/oauth2/token"):
            return Reply({"access_token": "tok", "expires_in": 300})
        return Reply({"verification_status": status}, ok=ok)

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def test_paypal_verifier_sends_the_documented_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_post(monkeypatch, "SUCCESS")
    assert paypal_verifier().verify(HEADERS, EVENT) is True
    call = calls[-1]
    assert (
        call["url"] == "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature"
    )
    assert call["json"]["webhook_id"] == "WEBHOOK-ID"
    assert call["json"]["transmission_id"] == "tx-1"
    assert call["json"]["webhook_event"] == EVENT
    assert call["headers"]["Authorization"] == "Bearer tok"


def test_paypal_verifier_rejects_failures_and_missing_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_post(monkeypatch, "FAILURE")
    assert paypal_verifier().verify(HEADERS, EVENT) is False
    calls = install_post(monkeypatch, "SUCCESS")
    assert paypal_verifier().verify({}, EVENT) is False
    assert (
        paypal_verifier().verify(
            {k: v for k, v in HEADERS.items() if k != "paypal-transmission-sig"}, EVENT
        )
        is False
    )
    assert calls == []


def test_paypal_verifier_fails_closed_when_paypal_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str, **kwargs: Any) -> Reply:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "post", boom)
    assert paypal_verifier().verify(HEADERS, EVENT) is False


def test_verifier_selection_is_secure_by_default() -> None:
    base = dataclasses.replace(
        make_settings(), payment_provider="paypal", paypal_client_id="id", paypal_client_secret="s"
    )
    assert isinstance(
        build_webhook_verifier(dataclasses.replace(base, webhook_verification="off")), NoopVerifier
    )
    assert isinstance(
        build_webhook_verifier(dataclasses.replace(base, webhook_id="W")), PayPalWebhookVerifier
    )
    assert isinstance(build_webhook_verifier(base), DenyAllVerifier)
    assert isinstance(
        build_webhook_verifier(dataclasses.replace(base, payment_provider="fake", webhook_id="W")),
        DenyAllVerifier,
    )
