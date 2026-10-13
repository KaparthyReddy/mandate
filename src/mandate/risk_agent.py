from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from mandate.anomaly import HISTORY_WINDOW, HistoryProvider, detect
from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest
from mandate.reputation import (
    RISK_BY_REPUTATION,
    Match,
    ReputationIndex,
    edit_distance,
    normalize_name,
    query_text,
)
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
        deny_similarity: float = 0.75,
        escalate_at: float = 0.4,
        lookalike_min: float = 0.55,
        lookalike_risk: float = 0.7,
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
        self._lookalike_min = lookalike_min
        self._lookalike_risk = lookalike_risk
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

    def _lookalike(self, matches: list[Match]) -> Match | None:
        if not matches or matches[0].similarity >= self._exact_similarity:
            return None
        for match in matches:
            near = self._lookalike_min <= match.similarity < self._exact_similarity
            if match.record.reputation == "trusted" and near:
                return match
        return None

    def _near_spelling(self, merchant: str) -> Match | None:
        name = normalize_name(merchant)
        trusted = [r for r in self._index.records if r.reputation == "trusted"]
        if any(normalize_name(r.name) == name for r in trusted):
            return None
        for record in trusted:
            other = normalize_name(record.name)
            if min(len(name), len(other)) < 5:
                continue
            distance = edit_distance(name, other)
            if distance <= 2:
                return Match(record=record, similarity=1 - distance / max(len(name), len(other)))
        return None

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote:
        now = self._clock()
        matches = self._index.search(query_text(request), k=5)
        reputation, source = self._reputation_score(matches)
        lookalike = self._lookalike(matches) or self._near_spelling(request.merchant)
        if lookalike is not None:
            reputation = max(reputation, self._lookalike_risk)
            source = "lookalike"
        history = self._history.recent_payments(mandate.mandate_id, now - HISTORY_WINDOW)
        anomalies = detect(mandate, request, spent, history, now)
        risk = min(1.0, self._reputation_weight * reputation + sum(a.score for a in anomalies))

        reasons: list[str] = []
        if lookalike is not None:
            reasons.append(
                f"merchant name resembles trusted brand '{lookalike.record.name}' but is not an "
                f"exact match (similarity {lookalike.similarity:.2f})"
            )
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
