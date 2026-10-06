from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from mandate import ledger, service
from mandate.config import get_private_key, get_public_key
from mandate.db import engine, get_session
from mandate.events import EventType
from mandate.mandates import SignedMandate
from mandate.models import Base, LedgerEntry, WebhookEvent
from mandate.policy import PaymentRequest
from mandate.provider import PaymentProvider, ProviderError, get_provider
from mandate.reviewers import Reviewer, get_reviewers
from mandate.service import MandateCreate, NotFoundError, StateError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="MANDATE", version="0.1.0", lifespan=lifespan)

SessionDep = Annotated[Session, Depends(get_session)]
PrivateKeyDep = Annotated[Ed25519PrivateKey, Depends(get_private_key)]
PublicKeyDep = Annotated[Ed25519PublicKey, Depends(get_public_key)]
ProviderDep = Annotated[PaymentProvider, Depends(get_provider)]
ReviewersDep = Annotated[Sequence[Reviewer], Depends(get_reviewers)]


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(StateError)
async def state_handler(_: Request, exc: StateError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ProviderError)
async def provider_handler(_: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mandates")
def create_mandate(
    body: MandateCreate,
    session: SessionDep,
    private_key: PrivateKeyDep,
) -> SignedMandate:
    try:
        return service.create_mandate(session, body, private_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/mandates")
def list_mandates(session: SessionDep) -> list[dict[str, Any]]:
    return service.list_mandate_views(session)


@app.get("/mandates/{mandate_id}")
def get_mandate(mandate_id: str, session: SessionDep) -> dict[str, Any]:
    return service.mandate_view(session, mandate_id)


@app.post("/mandates/{mandate_id}/revoke")
def revoke_mandate(mandate_id: str, session: SessionDep) -> dict[str, Any]:
    service.revoke_mandate(session, mandate_id)
    return service.mandate_view(session, mandate_id)


@app.post("/payments/request")
def request_payment(
    body: PaymentRequest,
    session: SessionDep,
    public_key: PublicKeyDep,
    provider: ProviderDep,
    reviewers: ReviewersDep,
) -> dict[str, Any]:
    record = service.process_payment(session, body, public_key, provider, reviewers)
    return service.payment_to_dict(record)


@app.get("/payments")
def list_payments(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    mandate_id: str | None = None,
) -> list[dict[str, Any]]:
    return service.list_payments(session, limit, mandate_id)


@app.get("/payments/{request_id}")
def get_payment(request_id: str, session: SessionDep) -> dict[str, Any]:
    return service.payment_to_dict(service.get_payment(session, request_id))


@app.post("/payments/{request_id}/approve")
def approve_payment(
    request_id: str,
    session: SessionDep,
    public_key: PublicKeyDep,
    provider: ProviderDep,
    approver: str = "human",
) -> dict[str, Any]:
    record = service.approve_payment(session, request_id, public_key, provider, approver)
    return service.payment_to_dict(record)


@app.post("/payments/{request_id}/reject")
def reject_payment(request_id: str, session: SessionDep, rejecter: str = "human") -> dict[str, Any]:
    return service.payment_to_dict(service.reject_payment(session, request_id, rejecter))


@app.post("/payments/{request_id}/capture")
def capture_payment(request_id: str, session: SessionDep, provider: ProviderDep) -> dict[str, Any]:
    return service.payment_to_dict(service.capture_payment(session, request_id, provider))


@app.post("/webhooks/paypal")
def paypal_webhook(event: dict[str, Any], session: SessionDep) -> dict[str, str]:
    event_id = str(event.get("id", ""))
    if not event_id:
        raise HTTPException(status_code=400, detail="missing event id")
    existing = session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
    if existing is None:
        event_type = str(event.get("event_type", "unknown"))
        session.add(WebhookEvent(event_id=event_id, event_type=event_type, payload=event))
        session.commit()
        ledger.append(
            session,
            EventType.WEBHOOK_RECEIVED,
            {"event_id": event_id, "event_type": event_type},
            actor="paypal",
        )
    return {"status": "received"}


@app.get("/webhooks/events")
def list_events(session: SessionDep) -> list[dict[str, str]]:
    rows = session.scalars(select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(50))
    return [
        {
            "event_id": r.event_id,
            "event_type": r.event_type,
            "received_at": r.received_at.isoformat(),
        }
        for r in rows
    ]


@app.get("/ledger")
def list_ledger(
    session: SessionDep,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.seq > after_seq)
        .order_by(LedgerEntry.seq)
        .limit(limit)
    )
    return [ledger.entry_to_dict(r) for r in rows]


@app.get("/ledger/verify")
def verify_ledger(session: SessionDep) -> ledger.VerificationResult:
    return ledger.verify_chain(session)
