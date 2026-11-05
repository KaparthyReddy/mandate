from collections.abc import Sequence
from functools import lru_cache

from mandate.config import Settings, get_settings
from mandate.council import Council
from mandate.db import SessionLocal
from mandate.embeddings import build_embedder
from mandate.intent_agent import IntentAgent
from mandate.llm import build_llm
from mandate.reputation import ReputationIndex, load_records
from mandate.reviewers import Reviewer
from mandate.risk_agent import RiskAgent
from mandate.risk_history import DbHistoryProvider
from mandate.sentinel import InjectionSentinel


def build_intent_agents(settings: Settings) -> list[Reviewer]:
    models = settings.intent_models
    if len(models) == 1:
        return [IntentAgent(build_llm(settings, models[0]))]
    return [
        IntentAgent(build_llm(settings, model), name=f"intent-{model.replace(':', '-')}")
        for model in models
    ]


def build_risk_agent(settings: Settings) -> RiskAgent:
    index = ReputationIndex(load_records(), build_embedder(settings))
    return RiskAgent(index, DbHistoryProvider(SessionLocal))


def build_council(settings: Settings) -> list[Reviewer]:
    if not settings.council_enabled:
        return []
    agents: list[Reviewer] = []
    if settings.llm_enabled:
        agents.extend(build_intent_agents(settings))
        agents.append(InjectionSentinel(build_llm(settings)))
    else:
        agents.append(InjectionSentinel(None))
    if settings.risk_enabled:
        agents.append(build_risk_agent(settings))
    return [Council(agents)]


@lru_cache
def get_reviewers() -> Sequence[Reviewer]:
    return build_council(get_settings())
