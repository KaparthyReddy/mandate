from collections.abc import Sequence
from functools import lru_cache

from mandate.config import get_settings
from mandate.council import Council
from mandate.intent_agent import IntentAgent
from mandate.llm import build_llm
from mandate.reviewers import Reviewer
from mandate.sentinel import InjectionSentinel


@lru_cache
def get_reviewers() -> Sequence[Reviewer]:
    settings = get_settings()
    if not settings.council_enabled:
        return []
    llm = build_llm(settings)
    return [Council([IntentAgent(llm), InjectionSentinel(llm)])]
