from datetime import datetime, timedelta
from decimal import Decimal
from statistics import fmean, pstdev
from typing import NamedTuple, Protocol

from mandate.mandates import Mandate
from mandate.policy import PaymentRequest

HISTORY_WINDOW = timedelta(days=7)


class PaymentSnapshot(NamedTuple):
    amount: Decimal
    merchant: str
    created_at: datetime
    status: str


class AnomalySignal(NamedTuple):
    name: str
    score: float
    detail: str


class HistoryProvider(Protocol):
    def recent_payments(self, mandate_id: str, since: datetime) -> list[PaymentSnapshot]: ...


def detect(
    mandate: Mandate,
    request: PaymentRequest,
    spent: Decimal,
    history: list[PaymentSnapshot],
    now: datetime,
) -> list[AnomalySignal]:
    signals: list[AnomalySignal] = []
    amount = request.amount

    known_merchants = {item.merchant.lower() for item in history}
    if history and request.merchant.lower() not in known_merchants:
        signals.append(AnomalySignal("new_merchant", 0.10, "first payment to this merchant"))

    amounts = [float(item.amount) for item in history if item.status != "denied"]
    if len(amounts) >= 5:
        mean = fmean(amounts)
        spread = max(pstdev(amounts), mean * 0.1, 0.01)
        z = (float(amount) - mean) / spread
        if z >= 5:
            signals.append(
                AnomalySignal("amount_outlier", 0.50, f"amount is {z:.1f} deviations high")
            )
        elif z >= 3:
            signals.append(
                AnomalySignal("amount_outlier", 0.35, f"amount is {z:.1f} deviations high")
            )

    for label, limit in (
        ("approval threshold", mandate.approval_threshold),
        ("per-transaction cap", mandate.per_txn_cap),
    ):
        if limit * Decimal("0.9") <= amount <= limit:
            signals.append(AnomalySignal("near_limit", 0.20, f"amount sits just under the {label}"))
            break

    last_ten_minutes = [item for item in history if now - item.created_at <= timedelta(minutes=10)]
    denied_recently = [item for item in last_ten_minutes if item.status == "denied"]
    allowed_recently = [item for item in last_ten_minutes if item.status != "denied"]
    if len(allowed_recently) >= 10:
        signals.append(
            AnomalySignal("velocity", 0.50, f"{len(allowed_recently)} payments in 10 minutes")
        )
    elif len(allowed_recently) >= 5:
        signals.append(
            AnomalySignal("velocity", 0.30, f"{len(allowed_recently)} payments in 10 minutes")
        )
    if len(denied_recently) >= 6:
        signals.append(
            AnomalySignal(
                "repeated_denials", 0.50, f"{len(denied_recently)} denied requests in 10 minutes"
            )
        )
    elif len(denied_recently) >= 3:
        signals.append(
            AnomalySignal(
                "repeated_denials",
                0.30,
                f"{len(denied_recently)} denied requests in 10 minutes, possible probing",
            )
        )

    identical = [
        item
        for item in history
        if item.amount == amount and now - item.created_at <= timedelta(hours=1)
    ]
    if len(identical) >= 3:
        signals.append(
            AnomalySignal("repeated_amount", 0.15, f"same amount {len(identical)} times in an hour")
        )

    if (spent + amount) / mandate.budget_total >= Decimal("0.8"):
        signals.append(
            AnomalySignal("budget_burn", 0.15, "payment would use over 80% of the budget")
        )

    return signals
