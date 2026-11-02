import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mandate import ledger
from mandate.events import EventType
from mandate.models import ApiKeyRecord
from mandate.service import NotFoundError

KEY_PREFIX = "mk_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_key(session: Session, agent_id: str, label: str = "") -> tuple[ApiKeyRecord, str]:
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    record = ApiKeyRecord(
        key_id=f"k-{secrets.token_hex(4)}",
        agent_id=agent_id,
        label=label[:100],
        key_hash=hash_token(token),
        created_at=datetime.now(UTC).isoformat(),
        revoked=False,
    )
    session.add(record)
    session.commit()
    ledger.append(
        session,
        EventType.KEY_CREATED,
        {"key_id": record.key_id, "agent_id": agent_id, "label": record.label},
        actor="admin",
    )
    return record, token


def key_view(record: ApiKeyRecord) -> dict[str, Any]:
    return {
        "key_id": record.key_id,
        "agent_id": record.agent_id,
        "label": record.label,
        "created_at": record.created_at,
        "revoked": record.revoked,
    }


def list_keys(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(ApiKeyRecord).order_by(ApiKeyRecord.id.desc()))
    return [key_view(row) for row in rows]


def revoke_key(session: Session, key_id: str) -> dict[str, Any]:
    record = session.scalar(select(ApiKeyRecord).where(ApiKeyRecord.key_id == key_id))
    if record is None:
        raise NotFoundError(f"key {key_id} not found")
    if not record.revoked:
        record.revoked = True
        session.commit()
        ledger.append(session, EventType.KEY_REVOKED, {"key_id": key_id}, actor="admin")
    return key_view(record)


def find_active(session: Session, token: str) -> ApiKeyRecord | None:
    return session.scalar(
        select(ApiKeyRecord).where(
            ApiKeyRecord.key_hash == hash_token(token), ApiKeyRecord.revoked.is_(False)
        )
    )
