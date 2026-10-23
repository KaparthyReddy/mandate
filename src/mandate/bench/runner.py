import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mandate import crypto, service
from mandate.bench import pools
from mandate.bench.generate import Case
from mandate.council import Council
from mandate.embeddings import HashingEmbedder
from mandate.intent_agent import IntentAgent
from mandate.llm import LLMClient
from mandate.models import Base
from mandate.policy import PaymentRequest
from mandate.provider import FakeProvider
from mandate.reputation import ReputationIndex, load_records
from mandate.reviewers import Reviewer
from mandate.risk_agent import RiskAgent
from mandate.risk_history import DbHistoryProvider
from mandate.sentinel import InjectionSentinel
from mandate.service import MandateCreate

PAID_STATUSES = {"order_created", "executed", "approved"}
CONFIGS = [
    "limits",
    "risk",
    "sentinel_rules",
    "rules",
    "sentinel_llm",
    "intent",
    "full",
    "limits_allowlist",
    "rules_strict",
    "full_strict",
    "full_allowlist",
]
LLM_CONFIGS = {"sentinel_llm", "intent", "full", "full_strict", "full_allowlist"}
ALLOWLIST_CONFIGS = {"limits_allowlist", "full_allowlist"}
STRICT_CONFIGS = {"rules_strict", "full_strict"}
CONFIG_LABELS = {
    "none": "No protection",
    "limits": "Mandate limits only",
    "risk": "Limits + Risk Agent",
    "sentinel_rules": "Limits + Sentinel (rules)",
    "rules": "Limits + Risk + Sentinel (rules)",
    "sentinel_llm": "Limits + Sentinel (rules + LLM)",
    "intent": "Limits + Intent Agent (LLM)",
    "full": "Full council",
    "limits_allowlist": "Limits + approved-vendor list",
    "rules_strict": "Limits + Risk + Sentinel (rules), approve new payees",
    "full_strict": "Full council, approve new payees",
    "full_allowlist": "Full council + approved-vendor list",
}


class RequestOutcome(BaseModel):
    status: str
    reasons: list[str]


class CaseResult(BaseModel):
    case_id: str
    cls: str
    kind: str
    outcomes: list[RequestOutcome]
    seconds: float
    paid: int
    escalated: int
    blocked: int
    attack_succeeded: bool


def vendor_list(purpose_text: str) -> list[str]:
    for key, spec in pools.PURPOSES.items():
        if spec.text == purpose_text:
            trusted = {item[0] for item in pools.HONEST_TRUSTED[key]}
            small = {item[0] for item in pools.HONEST_SMALL_VENDORS[key]}
            return sorted(trusted | small)
    return []


def build_reviewers(
    config: str,
    llm: LLMClient | None,
    index: ReputationIndex,
    factory: sessionmaker[Session],
) -> list[Reviewer]:
    if config in LLM_CONFIGS and llm is None:
        raise ValueError(f"config {config} needs an LLM")
    risk = RiskAgent(index, DbHistoryProvider(factory))
    agents: dict[str, list[Reviewer]] = {
        "limits": [],
        "risk": [risk],
        "sentinel_rules": [InjectionSentinel(None)],
        "rules": [InjectionSentinel(None), risk],
        "sentinel_llm": [InjectionSentinel(llm)],
        "intent": [IntentAgent(llm)] if llm else [],
        "full": [IntentAgent(llm), InjectionSentinel(llm), risk] if llm else [],
        "limits_allowlist": [],
        "rules_strict": [InjectionSentinel(None), risk],
        "full_strict": [IntentAgent(llm), InjectionSentinel(llm), risk] if llm else [],
        "full_allowlist": [IntentAgent(llm), InjectionSentinel(llm), risk] if llm else [],
    }
    chosen = agents[config]
    return [Council(chosen)] if chosen else []


def run_case(
    case: Case,
    reviewers: list[Reviewer],
    session: Session,
    signing_key: Ed25519PrivateKey,
    provider: FakeProvider,
    strict: bool = False,
    allowlist: bool = False,
) -> CaseResult:
    started = time.perf_counter()
    create = MandateCreate(
        issuer="bench",
        agent_id="bench-agent",
        purpose=case.purpose,
        budget_total=Decimal(case.budget),
        per_txn_cap=Decimal(case.cap),
        approval_threshold=Decimal(case.threshold),
        allowed_categories=case.allowed_categories,
        allowed_merchants=case.allowed_merchants
        or (vendor_list(case.purpose) if allowlist else []),
        new_payee_approval=strict,
        ttl_minutes=case.ttl_minutes,
    )
    mandate_id = service.create_mandate(session, create, signing_key).mandate.mandate_id
    if case.revoked:
        service.revoke_mandate(session, mandate_id)
    outcomes: list[RequestOutcome] = []
    for index, item in enumerate(case.requests):
        request = PaymentRequest(
            request_id=f"{case.id}-{index}",
            mandate_id=mandate_id,
            agent_id=item.agent_id or "bench-agent",
            amount=Decimal(item.amount),
            currency=item.currency,
            merchant=item.merchant,
            category=item.category,
            description=item.description,
            evidence=item.evidence,
        )
        now = datetime.now(UTC) + timedelta(minutes=item.minutes_later)
        record = service.process_payment(
            session, request, signing_key.public_key(), provider, reviewers, now
        )
        outcomes.append(RequestOutcome(status=record.status, reasons=list(record.reasons)))
    paid = sum(o.status in PAID_STATUSES for o in outcomes)
    escalated = sum(o.status == "pending_approval" for o in outcomes)
    return CaseResult(
        case_id=case.id,
        cls=case.cls,
        kind=case.kind,
        outcomes=outcomes,
        seconds=time.perf_counter() - started,
        paid=paid,
        escalated=escalated,
        blocked=len(outcomes) - paid - escalated,
        attack_succeeded=case.kind == "attack" and paid > case.allowed_paid,
    )


def run_config(
    config: str,
    cases: list[Case],
    llm: LLMClient | None,
    workdir: Path,
    progress: Callable[[int, int], None] | None = None,
) -> list[CaseResult]:
    workdir.mkdir(parents=True, exist_ok=True)
    database = workdir / f"{config}.db"
    database.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    index = ReputationIndex(load_records(), HashingEmbedder())
    reviewers = build_reviewers(config, llm, index, factory)
    signing_key = crypto.generate_private_key()
    provider = FakeProvider()
    results: list[CaseResult] = []
    for number, case in enumerate(cases, start=1):
        with factory() as session:
            results.append(
                run_case(
                    case,
                    reviewers,
                    session,
                    signing_key,
                    provider,
                    strict=config in STRICT_CONFIGS,
                    allowlist=config in ALLOWLIST_CONFIGS,
                )
            )
        if progress:
            progress(number, len(cases))
    engine.dispose()
    return results
