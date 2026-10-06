from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from mandate.mandates import Mandate


class Decision(StrEnum):
    APPROVE = "approve"
    ESCALATE = "escalate"
    DENY = "deny"


class PaymentRequest(BaseModel):
    request_id: str
    mandate_id: str
    agent_id: str
    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    merchant: str
    category: str
    description: str = ""
    evidence: str = Field(default="", max_length=8000)


class PolicyResult(BaseModel):
    decision: Decision
    reasons: list[str]


def evaluate(
    mandate: Mandate,
    request: PaymentRequest,
    spent_so_far: Decimal,
    now: datetime | None = None,
) -> PolicyResult:
    moment = now or datetime.now(UTC)
    denials: list[str] = []

    if request.mandate_id != mandate.mandate_id:
        denials.append("request references a different mandate")
    if request.agent_id != mandate.agent_id:
        denials.append("agent is not the holder of this mandate")
    if moment < mandate.issued_at:
        denials.append("mandate is not valid yet")
    if moment >= mandate.expires_at:
        denials.append("mandate has expired")
    if request.currency != mandate.currency:
        denials.append(f"currency {request.currency} not allowed, mandate is {mandate.currency}")
    if request.amount > mandate.per_txn_cap:
        denials.append(f"amount {request.amount} exceeds per-transaction cap {mandate.per_txn_cap}")
    remaining = mandate.budget_total - spent_so_far
    if request.amount > remaining:
        denials.append(f"amount {request.amount} exceeds remaining budget {remaining}")
    if mandate.allowed_merchants:
        allowed = {m.lower() for m in mandate.allowed_merchants}
        if request.merchant.lower() not in allowed:
            denials.append(f"merchant {request.merchant} is not on the allowlist")
    if mandate.allowed_categories:
        allowed_cats = {c.lower() for c in mandate.allowed_categories}
        if request.category.lower() not in allowed_cats:
            denials.append(f"category {request.category} is not allowed")

    if denials:
        return PolicyResult(decision=Decision.DENY, reasons=denials)
    if request.amount > mandate.approval_threshold:
        reason = f"amount {request.amount} exceeds approval threshold {mandate.approval_threshold}"
        return PolicyResult(decision=Decision.ESCALATE, reasons=[reason])
    return PolicyResult(decision=Decision.APPROVE, reasons=["within mandate limits"])
