from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest

_SEVERITY = {Decision.APPROVE: 0, Decision.ESCALATE: 1, Decision.DENY: 2}


class Vote(BaseModel):
    reviewer: str
    decision: Decision
    reasons: list[str]


class Reviewer(Protocol):
    name: str

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote: ...


REVIEWERS: list[Reviewer] = []


def get_reviewers() -> Sequence[Reviewer]:
    return REVIEWERS


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
        if _SEVERITY[vote.decision] > _SEVERITY[final]:
            final = vote.decision
    return final, collected
