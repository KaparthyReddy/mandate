import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from mandate import crypto


class MandateError(Exception):
    pass


class InvalidSignatureError(MandateError):
    pass


class ExpiredError(MandateError):
    pass


class NotYetValidError(MandateError):
    pass


class Mandate(BaseModel):
    model_config = ConfigDict(frozen=True)

    mandate_id: str
    issuer: str
    agent_id: str
    purpose: str = Field(default="", max_length=500)
    currency: str = "USD"
    budget_total: Decimal = Field(gt=0)
    per_txn_cap: Decimal = Field(gt=0)
    approval_threshold: Decimal = Field(gt=0)
    allowed_categories: list[str] = Field(default_factory=list)
    allowed_merchants: list[str] = Field(default_factory=list)
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.per_txn_cap > self.budget_total:
            raise ValueError("per_txn_cap cannot exceed budget_total")
        if self.approval_threshold > self.per_txn_cap:
            raise ValueError("approval_threshold cannot exceed per_txn_cap")
        return self


class SignedMandate(BaseModel):
    mandate: Mandate
    signature: str


def canonical_bytes(mandate: Mandate) -> bytes:
    data = mandate.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def sign_mandate(mandate: Mandate, private_key: Ed25519PrivateKey) -> SignedMandate:
    return SignedMandate(
        mandate=mandate,
        signature=crypto.sign(private_key, canonical_bytes(mandate)),
    )


def verify_mandate(
    signed: SignedMandate,
    public_key: Ed25519PublicKey,
    now: datetime | None = None,
) -> Mandate:
    if not crypto.verify(public_key, canonical_bytes(signed.mandate), signed.signature):
        raise InvalidSignatureError("signature does not match mandate")
    moment = now or datetime.now(UTC)
    if moment < signed.mandate.issued_at:
        raise NotYetValidError("mandate is not valid yet")
    if moment >= signed.mandate.expires_at:
        raise ExpiredError("mandate has expired")
    return signed.mandate
