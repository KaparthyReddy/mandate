from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    entry_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[int] = mapped_column(default=1)


class MandateRecord(Base):
    __tablename__ = "mandates"

    mandate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signed: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[str] = mapped_column(String(40))


class PaymentRecord(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mandate_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str] = mapped_column(String(128))
    amount: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8))
    merchant: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(24), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    reasons: Mapped[list[str]] = mapped_column(JSON)
    provider_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    approval_url: Mapped[str | None] = mapped_column(String(512), default=None)
    capture_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
