from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from mandate.db import engine, get_session
from mandate.models import Base, WebhookEvent


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
        session.add(
            WebhookEvent(
                event_id=event_id,
                event_type=str(event.get("event_type", "unknown")),
                payload=event,
            )
        )
        session.commit()
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
