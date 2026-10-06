import time
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol

import requests
from pydantic import BaseModel

from mandate.config import Settings, get_settings
from mandate.policy import PaymentRequest


class ProviderError(Exception):
    pass


class ProviderOrder(BaseModel):
    order_id: str
    status: str
    approval_url: str | None = None


class ProviderCapture(BaseModel):
    capture_id: str
    status: str


class PaymentProvider(Protocol):
    def create_order(self, request: PaymentRequest) -> ProviderOrder: ...

    def capture_order(self, order_id: str, request_id: str) -> ProviderCapture: ...


class FakeProvider:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.orders: list[str] = []

    def create_order(self, request: PaymentRequest) -> ProviderOrder:
        if self.fail:
            raise ProviderError("fake provider failure")
        order_id = f"FAKE-{request.request_id}"
        self.orders.append(order_id)
        return ProviderOrder(
            order_id=order_id,
            status="PAYER_ACTION_REQUIRED",
            approval_url=f"https://fake.example/approve/{order_id}",
        )

    def capture_order(self, order_id: str, request_id: str) -> ProviderCapture:
        return ProviderCapture(capture_id=f"CAP-{order_id}", status="COMPLETED")


class PayPalProvider:
    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (client_id, client_secret)
        self._token = ""
        self._expires_at = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        response = requests.post(
            f"{self._base_url}/v1/oauth2/token",
            auth=self._auth,
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        if not response.ok:
            raise ProviderError(f"PayPal auth failed: {response.status_code}")
        data = response.json()
        self._token = str(data["access_token"])
        self._expires_at = time.time() + float(data.get("expires_in", 300))
        return self._token

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": idempotency_key[:100],
        }

    def create_order(self, request: PaymentRequest) -> ProviderOrder:
        amount = format(request.amount.quantize(Decimal("0.01")), "f")
        body: dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": request.request_id[:100],
                    "custom_id": request.request_id[:127],
                    "description": (request.description or request.merchant)[:127],
                    "amount": {"currency_code": request.currency, "value": amount},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": "https://example.com/return",
                        "cancel_url": "https://example.com/cancel",
                        "user_action": "PAY_NOW",
                    }
                }
            },
        }
        try:
            response = requests.post(
                f"{self._base_url}/v2/checkout/orders",
                headers=self._headers(request.request_id),
                json=body,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"PayPal unreachable: {exc}") from exc
        if not response.ok:
            raise ProviderError(f"PayPal {response.status_code}: {response.text[:300]}")
        data = response.json()
        link = next(
            (
                item["href"]
                for item in data.get("links", [])
                if item.get("rel") in ("payer-action", "approve")
            ),
            None,
        )
        return ProviderOrder(order_id=data["id"], status=data["status"], approval_url=link)

    def capture_order(self, order_id: str, request_id: str) -> ProviderCapture:
        try:
            response = requests.post(
                f"{self._base_url}/v2/checkout/orders/{order_id}/capture",
                headers=self._headers(f"cap-{request_id}"),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"PayPal unreachable: {exc}") from exc
        if not response.ok:
            raise ProviderError(f"PayPal {response.status_code}: {response.text[:300]}")
        data = response.json()
        capture = data["purchase_units"][0]["payments"]["captures"][0]
        return ProviderCapture(capture_id=capture["id"], status=capture["status"])


def build_provider(settings: Settings) -> PaymentProvider:
    if settings.payment_provider == "fake":
        return FakeProvider()
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        raise RuntimeError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set")
    return PayPalProvider(
        settings.paypal_base_url,
        settings.paypal_client_id,
        settings.paypal_client_secret,
    )


@lru_cache
def get_provider() -> PaymentProvider:
    return build_provider(get_settings())
