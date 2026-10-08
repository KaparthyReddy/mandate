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
from mandate.reviewers import stricter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/intent_cases.jsonl")
    parser.add_argument("--model", default=None)
    parser.add_argument("--ensemble", default=None, help="comma-separated models, strictest wins")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if args.ensemble:
        models = [m.strip() for m in args.ensemble.split(",") if m.strip()]
    else:
        models = [args.model or settings.ollama_model]
    single = len(models) == 1
    agents = [
        IntentAgent(OllamaClient(settings.ollama_url, m, settings.llm_timeout), name=m)
        for m in models
    ]
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
            votes = [agent.review(mandate, request, Decimal("0")) for agent in agents]
        except LLMError as exc:
            errors += 1
            print(f"[{index:02d}] ERROR {exc}")
            continue
        latencies.append(time.perf_counter() - started)

        decision = Decision.APPROVE
        for vote in votes:
            decision = stricter(decision, vote.decision)
        verdicts = "/".join(str(v.details.get("verdict")) for v in votes)
        confidences = "/".join(f"{float(v.details.get('confidence', 0)):.2f}" for v in votes)
        expected = case["expected"]
        safe = (decision == Decision.APPROVE) == (expected == "match")
        binary += safe
        if single:
            exact += verdicts == expected
        if expected == "mismatch" and decision == Decision.APPROVE:
            bad_approved += 1
        if expected == "unclear" and decision == Decision.APPROVE:
            ambiguous_approved += 1
        if expected == "match" and decision == Decision.DENY:
            legit_denied += 1
        if expected == "match" and decision == Decision.ESCALATE:
            legit_escalated += 1
        ok = verdicts == expected if single else safe
        if not args.quiet or not ok:
            mark = "ok  " if ok else "MISS"
            print(
                f"[{index:02d}] {mark} expected={expected:<8} got={verdicts:<24} "
                f"conf={confidences:<10} decision={decision.value:<8} "
                f"{case['merchant']}: {case['description'][:40]}"
            )

    scored = len(cases) - errors
    print()
    print(f"models: {', '.join(models)}{'' if single else '  (ensemble, strictest wins)'}")
    print(f"cases: {len(cases)}  errors: {errors}")
    if scored:
        if single:
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
