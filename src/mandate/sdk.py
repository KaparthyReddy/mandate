import uuid
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Protocol, Self

import httpx
from pydantic import BaseModel, Field

APPROVED_STATUSES = {"approved", "order_created", "executed"}
BLOCKED_STATUSES = {"denied", "rejected"}


class MandateAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class PaymentResult(BaseModel):
    request_id: str
    mandate_id: str
    agent_id: str
    amount: Decimal
    currency: str = "USD"
    merchant: str
    category: str
    description: str = ""
    status: str
    decision: str
    reasons: list[str] = Field(default_factory=list)
    order_id: str | None = None
    approval_url: str | None = None
    capture_id: str | None = None

    @property
    def approved(self) -> bool:
        return self.status in APPROVED_STATUSES

    @property
    def needs_human(self) -> bool:
        return self.status == "pending_approval"

    @property
    def blocked(self) -> bool:
        return self.status in BLOCKED_STATUSES

    @property
    def failed(self) -> bool:
        return self.status == "failed"


class MandateStatus(BaseModel):
    mandate_id: str
    purpose: str
    agent_id: str
    currency: str
    status: str
    budget_total: Decimal
    per_txn_cap: Decimal
    approval_threshold: Decimal
    spent: Decimal
    remaining: Decimal
    expires_at: datetime
    allowed_categories: list[str] = Field(default_factory=list)
    allowed_merchants: list[str] = Field(default_factory=list)

    @classmethod
    def from_view(cls, view: dict[str, Any]) -> Self:
        mandate = view["mandate"]["mandate"]
        return cls(
            **{key: mandate[key] for key in cls.model_fields if key in mandate},
            status=view["status"],
            spent=view["spent"],
            remaining=view["remaining"],
        )


class HTTPResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


class HTTPClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: Any = None,
        headers: Any = None,
    ) -> HTTPResponse: ...


def _detail(response: HTTPResponse) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    detail = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', [])[1:])}: {item.get('msg', '')}"
            for item in detail
            if isinstance(item, dict)
        )
    return str(detail)


class MandateClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        api_key: str | None = None,
        timeout: float = 300.0,
        http: HTTPClient | None = None,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._owned: httpx.Client | None = None
        if http is None:
            self._owned = httpx.Client(base_url=base_url, timeout=timeout)
            http = self._owned
        self._http: HTTPClient = http

    def close(self) -> None:
        if self._owned is not None:
            self._owned.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
    ) -> Any:
        response = self._http.request(
            method, path, json=json, params=params, headers=self._headers or None
        )
        if response.status_code >= 400:
            raise MandateAPIError(response.status_code, _detail(response))
        return response.json()

    def create_mandate(
        self,
        *,
        purpose: str,
        agent_id: str,
        budget_total: Decimal | str,
        per_txn_cap: Decimal | str,
        approval_threshold: Decimal | str,
        issuer: str = "sdk",
        allowed_categories: list[str] | None = None,
        allowed_merchants: list[str] | None = None,
        currency: str = "USD",
        ttl_minutes: int = 1440,
    ) -> MandateStatus:
        signed = self._call(
            "POST",
            "/mandates",
            json={
                "issuer": issuer,
                "purpose": purpose,
                "agent_id": agent_id,
                "currency": currency,
                "budget_total": str(budget_total),
                "per_txn_cap": str(per_txn_cap),
                "approval_threshold": str(approval_threshold),
                "allowed_categories": allowed_categories or [],
                "allowed_merchants": allowed_merchants or [],
                "ttl_minutes": ttl_minutes,
            },
        )
        return self.get_mandate(signed["mandate"]["mandate_id"])

    def get_mandate(self, mandate_id: str) -> MandateStatus:
        return MandateStatus.from_view(self._call("GET", f"/mandates/{mandate_id}"))

    def request_payment(
        self,
        *,
        mandate_id: str,
        agent_id: str,
        amount: Decimal | str,
        merchant: str,
        category: str,
        description: str = "",
        evidence: str = "",
        currency: str = "USD",
        request_id: str | None = None,
    ) -> PaymentResult:
        body = {
            "request_id": request_id or f"req-{uuid.uuid4().hex[:16]}",
            "mandate_id": mandate_id,
            "agent_id": agent_id,
            "amount": str(amount),
            "currency": currency,
            "merchant": merchant,
            "category": category,
            "description": description,
            "evidence": evidence,
        }
        return PaymentResult.model_validate(self._call("POST", "/payments/request", json=body))

    def get_payment(self, request_id: str) -> PaymentResult:
        return PaymentResult.model_validate(self._call("GET", f"/payments/{request_id}"))

    def list_payments(self, mandate_id: str | None = None, limit: int = 50) -> list[PaymentResult]:
        params: dict[str, Any] = {"limit": limit}
        if mandate_id is not None:
            params["mandate_id"] = mandate_id
        return [
            PaymentResult.model_validate(item)
            for item in self._call("GET", "/payments", params=params)
        ]
