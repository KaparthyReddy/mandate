from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Protocol

import requests

from mandate.config import Settings, get_settings
from mandate.provider import PayPalProvider, ProviderError

SIGNATURE_HEADERS = [
    "paypal-auth-algo",
    "paypal-cert-url",
    "paypal-transmission-id",
    "paypal-transmission-sig",
    "paypal-transmission-time",
]


class WebhookVerifier(Protocol):
    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool: ...


class NoopVerifier:
    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        return True


class DenyAllVerifier:
    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        return False


class FakeVerifier:
    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.calls = 0

    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        self.calls += 1
        return self.accept


class PayPalWebhookVerifier:
    def __init__(self, provider: PayPalProvider, webhook_id: str) -> None:
        self._provider = provider
        self._webhook_id = webhook_id

    def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        values = {name: headers.get(name) for name in SIGNATURE_HEADERS}
        if not all(values.values()):
            return False
        body = {
            "auth_algo": values["paypal-auth-algo"],
            "cert_url": values["paypal-cert-url"],
            "transmission_id": values["paypal-transmission-id"],
            "transmission_sig": values["paypal-transmission-sig"],
            "transmission_time": values["paypal-transmission-time"],
            "webhook_id": self._webhook_id,
            "webhook_event": event,
        }
        try:
            response = requests.post(
                f"{self._provider.base_url}/v1/notifications/verify-webhook-signature",
                headers={
                    "Authorization": f"Bearer {self._provider.access_token()}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=30,
            )
            return bool(response.ok and response.json().get("verification_status") == "SUCCESS")
        except (requests.RequestException, ProviderError, ValueError):
            return False


def build_webhook_verifier(settings: Settings) -> WebhookVerifier:
    if settings.webhook_verification.lower() == "off":
        return NoopVerifier()
    has_credentials = settings.paypal_client_id and settings.paypal_client_secret
    if settings.webhook_id and settings.payment_provider != "fake" and has_credentials:
        provider = PayPalProvider(
            settings.paypal_base_url, settings.paypal_client_id, settings.paypal_client_secret
        )
        return PayPalWebhookVerifier(provider, settings.webhook_id)
    return DenyAllVerifier()


@lru_cache
def get_webhook_verifier() -> WebhookVerifier:
    return build_webhook_verifier(get_settings())
