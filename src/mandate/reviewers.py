from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field

from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest

_SEVERITY = {Decision.APPROVE: 0, Decision.ESCALATE: 1, Decision.DENY: 2}


class Vote(BaseModel):
    reviewer: str
    decision: Decision
    reasons: list[str]
    details: dict[str, Any] = Field(default_factory=dict)


class Reviewer(Protocol):
    name: str

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote: ...


def stricter(first: Decision, second: Decision) -> Decision:
    return first if _SEVERITY[first] >= _SEVERITY[second] else second


def combine(
    decision: Decision,
    reasons: list[str],
    votes: Sequence[Vote],
) -> tuple[Decision, list[str]]:
    final = decision
    collected = list(reasons)
    for vote in votes:
        if vote.decision != Decision.APPROVE:
            collected.extend(f"{vote.reviewer}: {reason}" for reason in vote.reasons)
        final = stricter(final, vote.decision)
    return final, collected
