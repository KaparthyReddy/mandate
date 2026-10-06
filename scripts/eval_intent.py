import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from mandate.config import get_settings
from mandate.intent_agent import IntentAgent
from mandate.llm import LLMError, OllamaClient
from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/intent_cases.jsonl")
    parser.add_argument("--model", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.ollama_model
    agent = IntentAgent(OllamaClient(settings.ollama_url, model, settings.llm_timeout))
    cases = [json.loads(line) for line in Path(args.cases).read_text().splitlines() if line.strip()]

    now = datetime.now(UTC)
    exact = binary = errors = 0
    bad_approved = ambiguous_approved = legit_denied = legit_escalated = 0
    latencies: list[float] = []

    for index, case in enumerate(cases, start=1):
        mandate = Mandate(
            mandate_id="m-eval",
            issuer="eval",
            agent_id="agent-eval",
            purpose=case["purpose"],
            budget_total=Decimal("1000"),
            per_txn_cap=Decimal("1000"),
            approval_threshold=Decimal("1000"),
            issued_at=now,
            expires_at=now + timedelta(days=1),
        )
        request = PaymentRequest(
            request_id=f"eval-{index}",
            mandate_id="m-eval",
            agent_id="agent-eval",
            amount=Decimal(case["amount"]),
            merchant=case["merchant"],
            category=case["category"],
            description=case["description"],
        )
        started = time.perf_counter()
        try:
            vote = agent.review(mandate, request, Decimal("0"))
        except LLMError as exc:
            errors += 1
            print(f"[{index:02d}] ERROR {exc}")
            continue
        latencies.append(time.perf_counter() - started)

        verdict = str(vote.details.get("verdict"))
        confidence = float(vote.details.get("confidence", 0))
        expected = case["expected"]
        exact += verdict == expected
        binary += (vote.decision == Decision.APPROVE) == (expected == "match")
        if expected == "mismatch" and vote.decision == Decision.APPROVE:
            bad_approved += 1
        if expected == "unclear" and vote.decision == Decision.APPROVE:
            ambiguous_approved += 1
        if expected == "match" and vote.decision == Decision.DENY:
            legit_denied += 1
        if expected == "match" and vote.decision == Decision.ESCALATE:
            legit_escalated += 1
        ok = verdict == expected
        if not args.quiet or not ok:
            mark = "ok  " if ok else "MISS"
            print(
                f"[{index:02d}] {mark} expected={expected:<8} got={verdict:<8} "
                f"conf={confidence:.2f} decision={vote.decision.value:<8} "
                f"{case['merchant']}: {case['description'][:45]}"
            )

    scored = len(cases) - errors
    print()
    print(f"model: {model}")
    print(f"cases: {len(cases)}  errors: {errors}")
    if scored:
        print(f"verdict accuracy: {exact}/{scored} ({exact / scored:.0%})")
        print(f"approve-vs-not accuracy: {binary}/{scored} ({binary / scored:.0%})")
        print(f"BAD payment approved (critical): {bad_approved}")
        print(f"ambiguous payment approved: {ambiguous_approved}")
        print(f"legit payment denied outright: {legit_denied}")
        print(f"legit payment sent to human: {legit_escalated}")
    if latencies:
        print(f"avg latency: {sum(latencies) / len(latencies):.1f}s")


if __name__ == "__main__":
    main()
