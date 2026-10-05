import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.environ["PAYPAL_BASE_URL"]
AUTH = (os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])


def get_token() -> str:
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=AUTH,
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "PayPal-Request-Id": str(uuid.uuid4()),
    }


def create_order(token: str) -> dict[str, object]:
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{"amount": {"currency_code": "USD", "value": "10.00"}}],
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
    r = requests.post(
        f"{BASE}/v2/checkout/orders",
        headers=auth_headers(token),
        json=body,
        timeout=30,
    )
    if not r.ok:
        print(r.status_code, r.text)
        sys.exit(1)
    return dict(r.json())


def capture_order(token: str, order_id: str) -> dict[str, object]:
    r = requests.post(
        f"{BASE}/v2/checkout/orders/{order_id}/capture",
        headers=auth_headers(token),
        timeout=30,
    )
    if not r.ok:
        print(r.status_code, r.text)
        sys.exit(1)
    return dict(r.json())


def main() -> None:
    token = get_token()
    order = create_order(token)
    print("order id:", order["id"], "status:", order["status"])
    link = next(
        item["href"]
        for item in order["links"]  # type: ignore[attr-defined]
        if item["rel"] in ("payer-action", "approve")
    )
    print("\nOpen this link in a private window and log in with your sandbox PERSONAL account:")
    print(link)
    input("\nPress Enter after approving...")
    result = capture_order(token, str(order["id"]))
    capture = result["purchase_units"][0]["payments"]["captures"][0]  # type: ignore[index]
    print("status:", result["status"])
    print("capture id:", capture["id"])
    print("amount:", capture["amount"]["value"], capture["amount"]["currency_code"])


if __name__ == "__main__":
    main()
