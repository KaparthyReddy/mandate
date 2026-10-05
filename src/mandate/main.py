from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from mandate import ledger
from mandate.db import engine, get_session
from mandate.events import EventType
from mandate.models import Base, LedgerEntry, WebhookEvent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="MANDATE", version="0.1.0", lifespan=lifespan)

SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
