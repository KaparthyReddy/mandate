from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from mandate.sdk import MandateAPIError, MandateClient


class Order(BaseModel):
    product_id: str
    merchant: str
    category: str
    amount: Decimal
    description: str
    evidence: str = ""


class PayResult(BaseModel):
    paid: bool
    status: str
    message: str
    reasons: list[str] = Field(default_factory=list)


class Payer(Protocol):
    def pay(self, order: Order) -> PayResult: ...


class NaivePayer:
    def pay(self, order: Order) -> PayResult:
        return PayResult(paid=True, status="paid", message="Payment completed.")


class MandatePayer:
    def __init__(self, client: MandateClient, mandate_id: str, agent_id: str) -> None:
        self._client = client
        self._mandate_id = mandate_id
        self._agent_id = agent_id

    def pay(self, order: Order) -> PayResult:
        try:
            result = self._client.request_payment(
                mandate_id=self._mandate_id,
                agent_id=self._agent_id,
                amount=order.amount,
                merchant=order.merchant,
                category=order.category,
                description=order.description,
                evidence=order.evidence,
            )
        except MandateAPIError as error:
            return PayResult(
                paid=False, status="error", message=f"Payment rejected: {error.detail}"
            )
        if result.approved:
            message = "Payment approved. The order was created and the user will finish checkout."
        elif result.needs_human:
            message = "Payment needs human approval and is waiting. Do not retry it."
        elif result.blocked:
            message = (
                f"Payment blocked: {'; '.join(result.reasons[:2])}. Do not retry or change the "
                "details to get around the block. Tell the user why."
            )
        else:
            message = "The payment provider failed. Tell the user."
        return PayResult(
            paid=result.approved, status=result.status, message=message, reasons=result.reasons
        )
