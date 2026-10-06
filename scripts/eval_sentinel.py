import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from mandate.config import get_settings
from mandate.llm import OllamaClient
from mandate.mandates import Mandate
from mandate.policy import Decision, PaymentRequest
from mandate.sentinel import InjectionSentinel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/sentinel_cases.jsonl")
    parser.add_argument("--model", default=None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.ollama_model
    llm = None if args.no_llm else OllamaClient(settings.ollama_url, model, settings.llm_timeout)
    sentinel = InjectionSentinel(llm)
    cases = [json.loads(line) for line in Path(args.cases).read_text().splitlines() if line.strip()]

    now = datetime.now(UTC)
    mandate = Mandate(
        mandate_id="m-eval",
        issuer="eval",
        agent_id="agent-eval",
        budget_total=Decimal("1000"),
        per_txn_cap=Decimal("1000"),
        approval_threshold=Decimal("1000"),
        issued_at=now,
        expires_at=now + timedelta(days=1),
    )

    attacks = clean = denied = caught = clean_ok = clean_blocked = 0
    latencies: list[float] = []

    for index, case in enumerate(cases, start=1):
        request = PaymentRequest(
            request_id=f"eval-{index}",
            mandate_id="m-eval",
            agent_id="agent-eval",
            amount=Decimal("10"),
            merchant=case["merchant"],
            category=case["category"],
            description=case["description"],
            evidence=case["evidence"],
        )
        started = time.perf_counter()
        vote = sentinel.review(mandate, request, Decimal("0"))
        latencies.append(time.perf_counter() - started)

        is_attack = case["expected"] == "attack"
        if is_attack:
            attacks += 1
            caught += vote.decision != Decision.APPROVE
            denied += vote.decision == Decision.DENY
            ok = vote.decision != Decision.APPROVE
        else:
            clean += 1
            clean_ok += vote.decision == Decision.APPROVE
            clean_blocked += vote.decision != Decision.APPROVE
            ok = vote.decision == Decision.APPROVE
        if not args.quiet or not ok:
            mark = "ok  " if ok else "MISS"
            print(
                f"[{case['id']}] {mark} {case['expected']:<6} decision={vote.decision.value:<8} "
                f"{case['tag']}"
            )

    print()
    print(f"mode: {'heuristics only' if args.no_llm else f'heuristics + {model}'}")
    print(f"attacks: {attacks}  clean: {clean}")
    print(f"attacks caught (escalated or denied): {caught}/{attacks}")
    print(f"attacks denied outright: {denied}/{attacks}")
    print(f"clean approved: {clean_ok}/{clean}")
    print(f"clean wrongly blocked: {clean_blocked}/{clean}")
    print(f"avg latency: {sum(latencies) / len(latencies):.2f}s")


if __name__ == "__main__":
    main()
