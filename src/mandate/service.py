import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mandate import ledger, policy
from mandate.events import EventType
from mandate.mandates import (
    Mandate,
    MandateError,
    SignedMandate,
    sign_mandate,
    verify_mandate,
)
from mandate.models import MandateRecord, PaymentRecord
from mandate.policy import Decision, PaymentRequest
from mandate.provider import PaymentProvider, ProviderError
from mandate.reviewers import Reviewer, Vote, combine

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    pass


class NotFoundError(ServiceError):
    pass


class StateError(ServiceError):
    pass


class PaymentStatus(StrEnum):
    APPROVED = "approved"
    PENDING_APPROVAL = "pending_approval"
    ORDER_CREATED = "order_created"
    EXECUTED = "executed"
    DENIED = "denied"
    REJECTED = "rejected"
    FAILED = "failed"


RESERVING = (
    PaymentStatus.APPROVED.value,
    PaymentStatus.PENDING_APPROVAL.value,
    PaymentStatus.ORDER_CREATED.value,
    PaymentStatus.EXECUTED.value,
)


class MandateCreate(BaseModel):
    issuer: str
    agent_id: str
    purpose: str = Field(default="", max_length=500)
    currency: str = "USD"
    budget_total: Decimal = Field(gt=0)
    per_txn_cap: Decimal = Field(gt=0)
    approval_threshold: Decimal = Field(gt=0)
    allowed_categories: list[str] = Field(default_factory=list)
    allowed_merchants: list[str] = Field(default_factory=list)
    ttl_minutes: int = Field(default=1440, gt=0, le=525600)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def issue_mandate(
    session: Session,
    mandate: Mandate,
    private_key: Ed25519PrivateKey,
) -> SignedMandate:
    signed = sign_mandate(mandate, private_key)
    session.add(
        MandateRecord(
            mandate_id=mandate.mandate_id,
            signed=signed.model_dump(mode="json"),
            status="active",
            created_at=_now_iso(),
        )
    )
    session.commit()
    ledger.append(
        session,
        EventType.MANDATE_ISSUED,
        mandate.model_dump(mode="json"),
        actor=mandate.issuer,
    )
    return signed


def create_mandate(
    session: Session,
    create: MandateCreate,
    private_key: Ed25519PrivateKey,
    now: datetime | None = None,
) -> SignedMandate:
    moment = now or datetime.now(UTC)
    mandate = Mandate(
        mandate_id=f"m-{uuid.uuid4().hex[:12]}",
        issuer=create.issuer,
        agent_id=create.agent_id,
        purpose=create.purpose,
        currency=create.currency,
        budget_total=create.budget_total,
        per_txn_cap=create.per_txn_cap,
        approval_threshold=create.approval_threshold,
        allowed_categories=create.allowed_categories,
        allowed_merchants=create.allowed_merchants,
        issued_at=moment,
        expires_at=moment + timedelta(minutes=create.ttl_minutes),
    )
    return issue_mandate(session, mandate, private_key)


def _get_mandate_record(session: Session, mandate_id: str) -> MandateRecord:
    record = session.get(MandateRecord, mandate_id)
    if record is None:
        raise NotFoundError(f"mandate {mandate_id} not found")
    return record


def revoke_mandate(session: Session, mandate_id: str) -> None:
    record = _get_mandate_record(session, mandate_id)
    if record.status == "revoked":
        return
    record.status = "revoked"
    session.commit()
    signed = SignedMandate.model_validate(record.signed)
    ledger.append(
        session,
        EventType.MANDATE_REVOKED,
        {"mandate_id": mandate_id},
        actor=signed.mandate.issuer,
    )


def kill_switch(session: Session, actor: str = "human") -> dict[str, int]:
    mandates = list(session.scalars(select(MandateRecord).where(MandateRecord.status == "active")))
    for record in mandates:
        record.status = "revoked"
    pending = list(
        session.scalars(
            select(PaymentRecord).where(
                PaymentRecord.status == PaymentStatus.PENDING_APPROVAL.value
            )
        )
    )
    for payment in pending:
        payment.status = PaymentStatus.REJECTED.value
        payment.reasons = [*payment.reasons, "kill switch activated"]
        payment.updated_at = _now_iso()
    session.commit()
    counts = {"mandates_revoked": len(mandates), "payments_rejected": len(pending)}
    ledger.append(session, EventType.KILL_SWITCH, counts, actor=actor)
    return counts


def spent_amount(session: Session, mandate_id: str) -> Decimal:
    rows = session.scalars(
        select(PaymentRecord.amount).where(
            PaymentRecord.mandate_id == mandate_id,
            PaymentRecord.status.in_(RESERVING),
        )
    )
    return sum((Decimal(row) for row in rows), Decimal("0"))


def mandate_view(session: Session, mandate_id: str) -> dict[str, Any]:
    record = _get_mandate_record(session, mandate_id)
    signed = SignedMandate.model_validate(record.signed)
    spent = spent_amount(session, mandate_id)
    return {
        "mandate": signed.model_dump(mode="json"),
        "status": record.status,
        "spent": _money(spent),
        "remaining": _money(signed.mandate.budget_total - spent),
    }


def list_mandate_views(session: Session, limit: int = 50) -> list[dict[str, Any]]:
    ids = session.scalars(
        select(MandateRecord.mandate_id).order_by(MandateRecord.created_at.desc()).limit(limit)
    )
    return [mandate_view(session, mandate_id) for mandate_id in list(ids)]


def _load_valid_mandate(
    session: Session,
    mandate_id: str,
    public_key: Ed25519PublicKey,
    now: datetime | None,
) -> tuple[Mandate | None, str | None]:
    record = session.get(MandateRecord, mandate_id)
    if record is None:
        return None, "unknown mandate"
    if record.status != "active":
        return None, "mandate has been revoked"
    try:
        mandate = verify_mandate(SignedMandate.model_validate(record.signed), public_key, now)
    except (MandateError, ValueError) as exc:
        return None, f"mandate invalid: {exc}"
    return mandate, None


def _collect_votes(
    reviewers: Sequence[Reviewer],
    mandate: Mandate,
    request: PaymentRequest,
    spent: Decimal,
) -> list[Vote]:
    votes: list[Vote] = []
    for reviewer in reviewers:
        try:
            votes.append(reviewer.review(mandate, request, spent))
        except Exception as exc:
            logger.exception("reviewer %s failed", reviewer.name)
            votes.append(
                Vote(
                    reviewer=reviewer.name,
                    decision=Decision.ESCALATE,
                    reasons=[f"reviewer failed: {exc}"],
                )
            )
    return votes


def _decide(
    session: Session,
    request: PaymentRequest,
    public_key: Ed25519PublicKey,
    reviewers: Sequence[Reviewer],
    now: datetime | None,
) -> tuple[Decision, list[str], list[Vote]]:
    mandate, problem = _load_valid_mandate(session, request.mandate_id, public_key, now)
    if mandate is None:
        return Decision.DENY, [problem or "mandate unavailable"], []
    spent = spent_amount(session, mandate.mandate_id)
    result = policy.evaluate(mandate, request, spent, now)
    if result.decision == Decision.DENY:
        return Decision.DENY, result.reasons, []
    votes = _collect_votes(reviewers, mandate, request, spent)
    base_reasons = [] if result.decision == Decision.APPROVE else result.reasons
    decision, reasons = combine(result.decision, base_reasons, votes)
    return decision, reasons or result.reasons, votes


def _find_payment(session: Session, request_id: str) -> PaymentRecord | None:
    return session.scalar(select(PaymentRecord).where(PaymentRecord.request_id == request_id))


def _get_payment(session: Session, request_id: str) -> PaymentRecord:
    record = _find_payment(session, request_id)
    if record is None:
        raise NotFoundError(f"payment {request_id} not found")
    return record


def get_payment(session: Session, request_id: str) -> PaymentRecord:
    return _get_payment(session, request_id)


def _request_from_record(record: PaymentRecord) -> PaymentRequest:
    return PaymentRequest(
        request_id=record.request_id,
        mandate_id=record.mandate_id,
        agent_id=record.agent_id,
        amount=Decimal(record.amount),
        currency=record.currency,
        merchant=record.merchant,
        category=record.category,
        description=record.description,
    )


def _open_order(session: Session, record: PaymentRecord, provider: PaymentProvider) -> None:
    try:
        order = provider.create_order(_request_from_record(record))
    except ProviderError as exc:
        record.status = PaymentStatus.FAILED.value
        record.reasons = [*record.reasons, f"provider error: {exc}"]
        record.updated_at = _now_iso()
        session.commit()
        ledger.append(
            session,
            EventType.PAYMENT_FAILED,
            {"request_id": record.request_id, "error": str(exc)},
            actor="provider",
        )
        return
    record.status = PaymentStatus.ORDER_CREATED.value
    record.provider_order_id = order.order_id
    record.approval_url = order.approval_url
    record.updated_at = _now_iso()
    session.commit()
    ledger.append(
        session,
        EventType.ORDER_CREATED,
        {"request_id": record.request_id, "order_id": order.order_id},
        actor="provider",
    )


def process_payment(
    session: Session,
    request: PaymentRequest,
    public_key: Ed25519PublicKey,
    provider: PaymentProvider,
    reviewers: Sequence[Reviewer] = (),
    now: datetime | None = None,
) -> PaymentRecord:
    existing = _find_payment(session, request.request_id)
    if existing is not None:
        return existing
    ledger.append(
        session,
        EventType.PAYMENT_REQUESTED,
        request.model_dump(mode="json"),
        actor=request.agent_id,
    )
    decision, reasons, votes = _decide(session, request, public_key, reviewers, now)
    status = {
        Decision.DENY: PaymentStatus.DENIED,
        Decision.ESCALATE: PaymentStatus.PENDING_APPROVAL,
        Decision.APPROVE: PaymentStatus.APPROVED,
    }[decision]
    stamp = _now_iso()
    record = PaymentRecord(
        request_id=request.request_id,
        mandate_id=request.mandate_id,
        agent_id=request.agent_id,
        amount=_money(request.amount),
        currency=request.currency,
        merchant=request.merchant,
        category=request.category,
        description=request.description[:512],
        status=status.value,
        decision=decision.value,
        reasons=reasons,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_payment(session, request.request_id)
        if existing is not None:
            return existing
        raise
    ledger.append(
        session,
        EventType.DECISION_MADE,
        {
            "request_id": record.request_id,
            "mandate_id": record.mandate_id,
            "amount": record.amount,
            "decision": record.decision,
            "status": record.status,
            "reasons": record.reasons,
            "votes": [vote.model_dump(mode="json") for vote in votes],
        },
        actor="mandate",
    )
    if decision == Decision.APPROVE:
        _open_order(session, record, provider)
    return record


def approve_payment(
    session: Session,
    request_id: str,
    public_key: Ed25519PublicKey,
    provider: PaymentProvider,
    approver: str = "human",
    now: datetime | None = None,
) -> PaymentRecord:
    record = _get_payment(session, request_id)
    if record.status != PaymentStatus.PENDING_APPROVAL.value:
        raise StateError(f"payment is {record.status}, not pending approval")
    mandate, problem = _load_valid_mandate(session, record.mandate_id, public_key, now)
    if mandate is None:
        record.status = PaymentStatus.REJECTED.value
        record.reasons = [*record.reasons, problem or "mandate unavailable"]
        record.updated_at = _now_iso()
        session.commit()
        ledger.append(
            session,
            EventType.PAYMENT_REJECTED,
            {"request_id": request_id, "reason": problem},
            actor="mandate",
        )
        return record
    record.status = PaymentStatus.APPROVED.value
    record.updated_at = _now_iso()
    session.commit()
    ledger.append(session, EventType.PAYMENT_APPROVED, {"request_id": request_id}, actor=approver)
    _open_order(session, record, provider)
    return record


def reject_payment(session: Session, request_id: str, rejecter: str = "human") -> PaymentRecord:
    record = _get_payment(session, request_id)
    if record.status != PaymentStatus.PENDING_APPROVAL.value:
        raise StateError(f"payment is {record.status}, not pending approval")
    record.status = PaymentStatus.REJECTED.value
    record.updated_at = _now_iso()
    session.commit()
    ledger.append(session, EventType.PAYMENT_REJECTED, {"request_id": request_id}, actor=rejecter)
    return record


def capture_payment(session: Session, request_id: str, provider: PaymentProvider) -> PaymentRecord:
    record = _get_payment(session, request_id)
    if record.status != PaymentStatus.ORDER_CREATED.value or not record.provider_order_id:
        raise StateError(f"payment is {record.status}, no open order to capture")
    capture = provider.capture_order(record.provider_order_id, record.request_id)
    if capture.status != "COMPLETED":
        raise ProviderError(f"capture status is {capture.status}")
    record.status = PaymentStatus.EXECUTED.value
    record.capture_id = capture.capture_id
    record.updated_at = _now_iso()
    session.commit()
    ledger.append(
        session,
        EventType.PAYMENT_EXECUTED,
        {
            "request_id": request_id,
            "order_id": record.provider_order_id,
            "capture_id": capture.capture_id,
            "amount": record.amount,
        },
        actor="provider",
    )
    return record


def payment_to_dict(record: PaymentRecord) -> dict[str, Any]:
    return {
        "request_id": record.request_id,
        "mandate_id": record.mandate_id,
        "agent_id": record.agent_id,
        "amount": record.amount,
        "currency": record.currency,
        "merchant": record.merchant,
        "category": record.category,
        "description": record.description,
        "status": record.status,
        "decision": record.decision,
        "reasons": record.reasons,
        "order_id": record.provider_order_id,
        "approval_url": record.approval_url,
        "capture_id": record.capture_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def list_payments(
    session: Session,
    limit: int = 50,
    mandate_id: str | None = None,
) -> list[dict[str, Any]]:
    query = select(PaymentRecord).order_by(PaymentRecord.id.desc()).limit(limit)
    if mandate_id is not None:
        query = query.where(PaymentRecord.mandate_id == mandate_id)
    return [payment_to_dict(row) for row in session.scalars(query)]
