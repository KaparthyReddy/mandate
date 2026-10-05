import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mandate.models import LedgerEntry

GENESIS_HASH = "0" * 64
SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)

Listener = Callable[[LedgerEntry], None]
_listeners: list[Listener] = []


class VerificationResult(BaseModel):
    valid: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None
    head_seq: int
    head_hash: str


def add_listener(listener: Listener) -> None:
    _listeners.append(listener)


def clear_listeners() -> None:
    _listeners.clear()


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(json.loads(json.dumps(payload, default=str, sort_keys=True)))


def compute_hash(
    *,
    seq: int,
    prev_hash: str,
    entry_type: str,
    actor: str,
    payload: dict[str, Any],
    created_at: str,
    schema_version: int,
) -> str:
    body = json.dumps(
        {
            "seq": seq,
            "prev_hash": prev_hash,
            "entry_type": entry_type,
            "actor": actor,
            "payload": payload,
            "created_at": created_at,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _last_entry(session: Session) -> LedgerEntry | None:
    return session.scalar(select(LedgerEntry).order_by(LedgerEntry.seq.desc()).limit(1))


def get_head(session: Session) -> tuple[int, str]:
    last = _last_entry(session)
    if last is None:
        return 0, GENESIS_HASH
    return last.seq, last.entry_hash


def append(
    session: Session,
    entry_type: str,
    payload: dict[str, Any],
    actor: str = "system",
) -> LedgerEntry:
    seq, prev_hash = get_head(session)
    seq += 1
    clean = normalize(payload)
    created_at = datetime.now(UTC).isoformat()
    entry = LedgerEntry(
        seq=seq,
        prev_hash=prev_hash,
        entry_hash=compute_hash(
            seq=seq,
            prev_hash=prev_hash,
            entry_type=str(entry_type),
            actor=actor,
            payload=clean,
            created_at=created_at,
            schema_version=SCHEMA_VERSION,
        ),
        entry_type=str(entry_type),
        actor=actor,
        payload=clean,
        created_at=created_at,
        schema_version=SCHEMA_VERSION,
    )
    session.add(entry)
    session.commit()
    for listener in list(_listeners):
        try:
            listener(entry)
        except Exception:
            logger.exception("ledger listener failed")
    return entry


def verify_chain(session: Session) -> VerificationResult:
    expected_seq = 1
    expected_prev = GENESIS_HASH
    checked = 0
    entries = session.scalars(select(LedgerEntry).order_by(LedgerEntry.seq))
    for entry in entries:
        problem: str | None = None
        if entry.seq != expected_seq:
            problem = f"sequence gap: expected {expected_seq}, found {entry.seq}"
        elif entry.prev_hash != expected_prev:
            problem = "prev_hash does not match previous entry"
        else:
            recomputed = compute_hash(
                seq=entry.seq,
                prev_hash=entry.prev_hash,
                entry_type=entry.entry_type,
                actor=entry.actor,
                payload=entry.payload,
                created_at=entry.created_at,
                schema_version=entry.schema_version,
            )
            if recomputed != entry.entry_hash:
                problem = "entry hash does not match contents"
        if problem is not None:
            head_seq, head_hash = get_head(session)
            return VerificationResult(
                valid=False,
                checked=checked,
                first_bad_seq=entry.seq,
                reason=problem,
                head_seq=head_seq,
                head_hash=head_hash,
            )
        expected_seq = entry.seq + 1
        expected_prev = entry.entry_hash
        checked += 1
    head_seq, head_hash = get_head(session)
    return VerificationResult(valid=True, checked=checked, head_seq=head_seq, head_hash=head_hash)


def entry_to_dict(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "entry_type": entry.entry_type,
        "actor": entry.actor,
        "payload": entry.payload,
        "created_at": entry.created_at,
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
        "schema_version": entry.schema_version,
    }
