from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mandate.anomaly import PaymentSnapshot
from mandate.models import PaymentRecord


class DbHistoryProvider:
    def __init__(self, session_factory: Callable[[], Session], limit: int = 200) -> None:
        self._session_factory = session_factory
        self._limit = limit

    def recent_payments(self, mandate_id: str, since: datetime) -> list[PaymentSnapshot]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(PaymentRecord)
                .where(PaymentRecord.mandate_id == mandate_id)
                .order_by(PaymentRecord.id.desc())
                .limit(self._limit)
            )
            snapshots = [
                PaymentSnapshot(
                    amount=Decimal(row.amount),
                    merchant=row.merchant,
                    created_at=datetime.fromisoformat(row.created_at),
                    status=row.status,
                )
                for row in rows
            ]
        return [item for item in snapshots if item.created_at >= since]
