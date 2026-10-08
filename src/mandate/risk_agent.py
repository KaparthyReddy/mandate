from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from mandate.anomaly import HISTORY_WINDOW, HistoryProvider, detect
from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest
from mandate.reputation import RISK_BY_REPUTATION, Match, ReputationIndex, query_text
from mandate.reviewers import Vote


class RiskAgent:
    name = "risk"

    def __init__(
        self,
        index: ReputationIndex,
        history: HistoryProvider,
        *,
        min_similarity: float = 0.6,
        exact_similarity: float = 0.95,
        deny_similarity: float = 0.85,
        escalate_at: float = 0.4,
        unknown_risk: float = 0.4,
        reputation_weight: float = 0.7,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._index = index
        self._history = history
        self._min_similarity = min_similarity
        self._exact_similarity = exact_similarity
        self._deny_similarity = deny_similarity
        self._escalate_at = escalate_at
        self._unknown_risk = unknown_risk
        self._reputation_weight = reputation_weight
        self._clock = clock or (lambda: datetime.now(UTC))

    def _reputation_score(self, matches: list[Match]) -> tuple[float, str]:
        if matches and matches[0].similarity >= self._exact_similarity:
            return RISK_BY_REPUTATION[matches[0].record.reputation], "exact"
        relevant = [m for m in matches if m.similarity >= self._min_similarity]
        if not relevant:
            return self._unknown_risk, "unknown"
        weights = [m.similarity**2 for m in relevant]
        total = sum(
            w * RISK_BY_REPUTATION[m.record.reputation]
            for w, m in zip(weights, relevant, strict=True)
        )
        return total / sum(weights), "similar"

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote:
        now = self._clock()
        matches = self._index.search(query_text(request), k=5)
        reputation, source = self._reputation_score(matches)
        history = self._history.recent_payments(mandate.mandate_id, now - HISTORY_WINDOW)
        anomalies = detect(mandate, request, spent, history, now)
        risk = min(1.0, self._reputation_weight * reputation + sum(a.score for a in anomalies))

        reasons: list[str] = []
        top = matches[0] if matches else None
        if top is not None and top.similarity >= self._min_similarity:
            reasons.append(
                f"merchant resembles '{top.record.name}' ({top.record.reputation}, "
                f"similarity {top.similarity:.2f}): {top.record.notes}"
            )
        elif source == "unknown":
            reasons.append("merchant not found in reputation data")
        reasons.extend(f"{a.name}: {a.detail}" for a in anomalies)

        blocked = (
            top is not None
            and top.record.reputation == "blocked"
            and top.similarity >= self._deny_similarity
        )
        if blocked:
            decision = Decision.DENY
        elif risk >= self._escalate_at:
            decision = Decision.ESCALATE
            reasons.append(f"risk score {risk:.2f} reached {self._escalate_at:.2f}")
        else:
            decision = Decision.APPROVE
            reasons = reasons or [f"risk score {risk:.2f} is low"]

        details: dict[str, Any] = {
            "risk": round(risk, 3),
            "reputation": {
                "score": round(reputation, 3),
                "source": source,
                "matches": [
                    {
                        "name": m.record.name,
                        "reputation": m.record.reputation,
                        "similarity": round(m.similarity, 3),
                    }
                    for m in matches[:3]
                ],
            },
            "anomalies": [a._asdict() for a in anomalies],
            "backend": self._index.backend,
        }
        return Vote(reviewer=self.name, decision=decision, reasons=reasons, details=details)
