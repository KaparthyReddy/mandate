import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mandate.auth import AuthConfig, RateLimiter, get_auth_config, get_rate_limiter
from mandate.db import SessionLocal
from mandate.main import app
from mandate.models import ApiKeyRecord
from mandate.sdk import MandateAPIError, MandateClient

ADMIN = "admin-secret-key"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture
def secured() -> Iterator[TestClient]:
    app.dependency_overrides[get_auth_config] = lambda: AuthConfig(True, ADMIN)
    app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(1000)
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()


def mandate_body(agent: str) -> dict[str, Any]:
    return {
        "issuer": "admin",
        "agent_id": agent,
        "budget_total": "500",
        "per_txn_cap": "100",
        "approval_threshold": "50",
    }


def new_mandate(http: TestClient, agent: str) -> str:
    response = http.post("/mandates", json=mandate_body(agent), headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return str(response.json()["mandate"]["mandate_id"])


def new_key(http: TestClient, agent: str) -> dict[str, str]:
    response = http.post(
        "/admin/keys", json={"agent_id": agent, "label": "test"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def pay_body(mandate_id: str, agent: str, request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "mandate_id": mandate_id,
        "agent_id": agent,
        "amount": "10.00",
        "merchant": "BigMart",
        "category": "groceries",
    }


def bearer(key: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['key']}"}


def test_health_and_dashboard_stay_public(secured: TestClient) -> None:
    assert secured.get("/health").status_code == 200
    assert secured.get("/").status_code == 200


def test_protected_endpoints_reject_missing_and_wrong_keys(secured: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        response = secured.get("/ledger/verify", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    assert secured.post("/mandates", json=mandate_body("a")).status_code == 401
    assert secured.post("/kill-switch").status_code == 401


def test_admin_key_opens_everything(secured: TestClient) -> None:
    assert secured.get("/ledger/verify", headers=ADMIN_HEADERS).status_code == 200
    assert secured.get("/mandates", headers=ADMIN_HEADERS).status_code == 200
    assert secured.get("/payments", headers=ADMIN_HEADERS).status_code == 200


def test_server_without_an_admin_key_refuses_instead_of_opening_up() -> None:
    app.dependency_overrides[get_auth_config] = lambda: AuthConfig(True, "")
    try:
        with TestClient(app) as http:
            response = http.get("/ledger/verify", headers=ADMIN_HEADERS)
        assert response.status_code == 503
        assert "MANDATE_ADMIN_KEY" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_agent_key_can_pay_only_as_its_own_agent(secured: TestClient) -> None:
    mandate_id = new_mandate(secured, "agent-a")
    key = new_key(secured, "agent-a")
    ok = secured.post(
        "/payments/request", json=pay_body(mandate_id, "agent-a", "a-1"), headers=bearer(key)
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "order_created"
    wrong = secured.post(
        "/payments/request", json=pay_body(mandate_id, "agent-b", "a-2"), headers=bearer(key)
    )
    assert wrong.status_code == 403


def test_agent_key_cannot_use_another_agents_mandate(secured: TestClient) -> None:
    other = new_mandate(secured, "agent-b")
    key = new_key(secured, "agent-a")
    response = secured.post(
        "/payments/request", json=pay_body(other, "agent-a", "x-1"), headers=bearer(key)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "denied"


def test_agent_key_cannot_do_admin_things(secured: TestClient) -> None:
    mandate_id = new_mandate(secured, "agent-a")
    key = new_key(secured, "agent-a")
    paid = secured.post(
        "/payments/request", json=pay_body(mandate_id, "agent-a", "p-1"), headers=bearer(key)
    )
    request_id = paid.json()["request_id"]
    attempts = [
        secured.post("/mandates", json=mandate_body("agent-a"), headers=bearer(key)),
        secured.post(f"/mandates/{mandate_id}/revoke", headers=bearer(key)),
        secured.post("/kill-switch", headers=bearer(key)),
        secured.post(f"/payments/{request_id}/approve", headers=bearer(key)),
        secured.post(f"/payments/{request_id}/capture", headers=bearer(key)),
        secured.get("/payments", headers=bearer(key)),
        secured.get("/mandates", headers=bearer(key)),
        secured.get("/ledger", headers=bearer(key)),
        secured.post("/admin/keys", json={"agent_id": "x"}, headers=bearer(key)),
        secured.get("/admin/keys", headers=bearer(key)),
    ]
    assert [r.status_code for r in attempts] == [403] * len(attempts)


def test_agent_key_sees_only_its_own_payments_and_mandates(secured: TestClient) -> None:
    mine, theirs = new_mandate(secured, "agent-a"), new_mandate(secured, "agent-b")
    key_a, key_b = new_key(secured, "agent-a"), new_key(secured, "agent-b")
    mine_payment = secured.post(
        "/payments/request", json=pay_body(mine, "agent-a", "m-1"), headers=bearer(key_a)
    )
    assert (
        secured.get(
            f"/payments/{mine_payment.json()['request_id']}", headers=bearer(key_a)
        ).status_code
        == 200
    )
    assert (
        secured.get(
            f"/payments/{mine_payment.json()['request_id']}", headers=bearer(key_b)
        ).status_code
        == 404
    )
    assert secured.get(f"/mandates/{mine}", headers=bearer(key_a)).status_code == 200
    assert secured.get(f"/mandates/{theirs}", headers=bearer(key_a)).status_code == 404


def test_revoked_keys_stop_working(secured: TestClient) -> None:
    key = new_key(secured, "agent-a")
    assert secured.get("/payments/none", headers=bearer(key)).status_code == 404
    revoked = secured.post(f"/admin/keys/{key['key_id']}/revoke", headers=ADMIN_HEADERS)
    assert revoked.json()["revoked"] is True
    assert secured.get("/payments/none", headers=bearer(key)).status_code == 401


def test_keys_are_stored_hashed_and_never_listed_or_logged(secured: TestClient) -> None:
    key = new_key(secured, "agent-a")
    with SessionLocal() as session:
        row = session.query(ApiKeyRecord).filter_by(key_id=key["key_id"]).one()
    assert row.key_hash == hashlib.sha256(key["key"].encode()).hexdigest()
    assert key["key"] not in row.key_hash
    listing = secured.get("/admin/keys", headers=ADMIN_HEADERS).text
    ledger_text = secured.get("/ledger", params={"limit": 500}, headers=ADMIN_HEADERS).text
    assert key["key"] not in listing
    assert key["key"] not in ledger_text
    assert "key_created" in ledger_text


def test_payment_requests_are_rate_limited_per_key() -> None:
    app.dependency_overrides[get_auth_config] = lambda: AuthConfig(True, ADMIN)
    limiter = RateLimiter(2)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    try:
        with TestClient(app) as http:
            mandate_id = new_mandate(http, "agent-a")
            key = new_key(http, "agent-a")
            codes = [
                http.post(
                    "/payments/request",
                    json=pay_body(mandate_id, "agent-a", f"r-{i}"),
                    headers=bearer(key),
                )
                for i in range(3)
            ]
            assert [r.status_code for r in codes] == [200, 200, 429]
            assert int(codes[2].headers["retry-after"]) >= 1
            other = new_key(http, "agent-a")
            assert (
                http.post(
                    "/payments/request",
                    json=pay_body(mandate_id, "agent-a", "r-9"),
                    headers=bearer(other),
                ).status_code
                == 200
            )
    finally:
        app.dependency_overrides.clear()


def test_rate_limiter_window_slides_and_zero_disables() -> None:
    limiter = RateLimiter(2)
    assert limiter.check("k", now=0) == 0
    assert limiter.check("k", now=1) == 0
    assert limiter.check("k", now=2) > 0
    assert limiter.check("k", now=61) == 0
    assert RateLimiter(0).check("k", now=0) == 0


def test_sdk_creates_agent_keys_and_the_key_confines_the_agent(secured: TestClient) -> None:
    admin = MandateClient(http=secured, api_key=ADMIN)
    mandate = admin.create_mandate(
        purpose="Weekly groceries",
        agent_id="agent-a",
        budget_total="500",
        per_txn_cap="100",
        approval_threshold="50",
    )
    agent = MandateClient(http=secured, api_key=admin.create_agent_key("agent-a", "ci"))
    result = agent.request_payment(
        mandate_id=mandate.mandate_id,
        agent_id="agent-a",
        amount="10.00",
        merchant="BigMart",
        category="groceries",
    )
    assert result.approved
    assert agent.get_mandate(mandate.mandate_id).mandate_id == mandate.mandate_id
    with pytest.raises(MandateAPIError) as error:
        agent.create_mandate(
            purpose="x",
            agent_id="agent-a",
            budget_total="5",
            per_txn_cap="5",
            approval_threshold="5",
        )
    assert error.value.status_code == 403


def test_sdk_reads_the_agent_key_from_the_environment_but_never_the_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=[])

    def client() -> MandateClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")
        return MandateClient(http=http)

    monkeypatch.setenv("MANDATE_ADMIN_KEY", "the-admin-key")
    monkeypatch.delenv("MANDATE_API_KEY", raising=False)
    client().list_payments()
    monkeypatch.setenv("MANDATE_API_KEY", "an-agent-key")
    client().list_payments()
    assert seen == ["", "Bearer an-agent-key"]
