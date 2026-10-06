import logging
import operator
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest
from mandate.reviewers import Reviewer, Vote, combine

logger = logging.getLogger(__name__)


class CouncilState(TypedDict):
    mandate: Mandate
    request: PaymentRequest
    spent: Decimal
    votes: Annotated[list[Vote], operator.add]


def _node(agent: Reviewer) -> Callable[[CouncilState], dict[str, list[Vote]]]:
    def run(state: CouncilState) -> dict[str, list[Vote]]:
        try:
            vote = agent.review(state["mandate"], state["request"], state["spent"])
        except Exception as exc:
            logger.exception("council agent %s failed", agent.name)
            vote = Vote(
                reviewer=agent.name,
                decision=Decision.ESCALATE,
                reasons=[f"agent failed: {exc}"],
            )
        return {"votes": [vote]}

    return run


class Council:
    name = "council"

    def __init__(self, agents: Sequence[Reviewer]) -> None:
        names = [agent.name for agent in agents]
        if len(set(names)) != len(names):
            raise ValueError("council agent names must be unique")
        self.agents = list(agents)
        self._order = {name: index for index, name in enumerate(names)}
        self._graph: Any = self._build() if self.agents else None

    def _build(self) -> Any:
        graph = StateGraph(CouncilState)
        for agent in self.agents:
            graph.add_node(agent.name, _node(agent))  # type: ignore[call-overload]
            graph.add_edge(START, agent.name)
            graph.add_edge(agent.name, END)
        return graph.compile()

    def review(self, mandate: Mandate, request: PaymentRequest, spent: Decimal) -> Vote:
        if not self.agents:
            return Vote(
                reviewer=self.name,
                decision=Decision.APPROVE,
                reasons=["no council agents configured"],
            )
        state = self._graph.invoke(
            {"mandate": mandate, "request": request, "spent": spent, "votes": []}
        )
        votes = sorted(state["votes"], key=lambda vote: self._order[vote.reviewer])
        decision, reasons = combine(Decision.APPROVE, [], votes)
        return Vote(
            reviewer=self.name,
            decision=decision,
            reasons=reasons or ["all council agents approve"],
            details={"votes": [vote.model_dump(mode="json") for vote in votes]},
        )
