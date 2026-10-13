from pathlib import Path

from mandate.bench.cache import CachingLLM
from mandate.bench.generate import ATTACK_CLASSES, HONEST_CLASSES, Generator, generate
from mandate.bench.report import render_markdown, summarize
from mandate.bench.runner import run_config
from mandate.llm import FakeLLM, LLMError


def dump(cases: list) -> list[dict]:  # type: ignore[type-arg]
    return [case.model_dump() for case in cases]


def test_generation_is_deterministic_and_seed_sensitive() -> None:
    assert dump(generate(7, "dev", 5, 0.1)) == dump(generate(7, "dev", 5, 0.1))
    assert dump(generate(7, "dev", 5, 0.1)) != dump(generate(8, "dev", 5, 0.1))
    assert dump(generate(7, "dev", 5, 0.1)) != dump(generate(7, "test", 5, 0.1))


def test_every_class_is_present_and_ids_are_unique() -> None:
    cases = generate(7, "test", 4, 0.2)
    classes = {case.cls for case in cases}
    assert set(ATTACK_CLASSES) | set(HONEST_CLASSES) == classes
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))


def test_dev_and_test_use_disjoint_template_halves() -> None:
    items = list(range(20))
    dev = {Generator(1, "dev").pick(items) for _ in range(1)}
    dev_gen, test_gen = Generator(1, "dev"), Generator(1, "test")
    assert all(dev_gen.pick(items) % 2 == 0 for _ in range(100))
    assert all(test_gen.pick(items) % 2 == 1 for _ in range(100))
    assert dev


def test_honest_requests_stay_inside_the_mandate_limits() -> None:
    for case in generate(7, "dev", 3, 0.5):
        if case.kind != "honest":
            continue
        for request in case.requests:
            assert 0 < float(request.amount) <= case.cap
            if case.cls == "above_threshold":
                assert float(request.amount) > case.threshold
            else:
                assert float(request.amount) <= case.threshold


def test_limits_alone_stop_every_policy_violation_and_pay_routine_purchases(tmp_path: Path) -> None:
    cases = generate(7, "dev", 6, 0.2)
    results = run_config("limits", cases, None, tmp_path)
    policy = [r for r in results if r.cls == "policy"]
    assert policy and not any(r.attack_succeeded for r in policy)
    routine = [r for r in results if r.cls == "routine"]
    assert routine and all(r.paid == 1 for r in routine)


def test_rules_catch_overrides_and_structuring_without_blocking_honest_text(tmp_path: Path) -> None:
    cases = generate(7, "dev", 10, 0.5)
    results = run_config("rules", cases, None, tmp_path)
    by_class: dict[str, list[bool]] = {}
    for result in results:
        by_class.setdefault(result.cls, []).append(result.attack_succeeded)
    assert not any(by_class["override"])
    assert not any(by_class["structuring"])
    assert sum(by_class["lookalike"]) < len(by_class["lookalike"])
    tricky = [r for r in results if r.cls == "tricky_text"]
    assert tricky and all(r.blocked == 0 for r in tricky)


def test_llm_configs_require_an_llm(tmp_path: Path) -> None:
    try:
        run_config("full", generate(7, "dev", 1, 0.05), None, tmp_path)
    except ValueError as error:
        assert "needs an LLM" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_report_contains_the_baseline_and_each_class(tmp_path: Path) -> None:
    cases = generate(7, "dev", 3, 0.1)
    summary = summarize(cases, {"limits": run_config("limits", cases, None, tmp_path)})
    text = render_markdown(summary, "Title")
    assert "No protection" in text
    assert "Mandate limits only" in text
    assert all(cls in text for cls in ATTACK_CLASSES)
    assert summary["configs"]["limits"]["attack_cases"] == 3 * len(ATTACK_CLASSES)


def test_llm_cache_serves_repeats_persists_and_skips_errors(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    inner = FakeLLM("answer")
    cache = CachingLLM(inner, path, salt="m")
    assert cache.complete_json("s", "u") == "answer"
    assert cache.complete_json("s", "u") == "answer"
    assert (cache.hits, cache.misses, len(inner.calls)) == (1, 1, 1)
    again = CachingLLM(FakeLLM("different"), path, salt="m")
    assert again.complete_json("s", "u") == "answer"
    assert CachingLLM(FakeLLM("other"), path, salt="n").complete_json("s", "u") == "other"

    class Broken:
        def complete_json(self, system: str, user: str) -> str:
            raise LLMError("down")

    failing = CachingLLM(Broken(), tmp_path / "e.jsonl")
    try:
        failing.complete_json("s", "u")
    except LLMError:
        pass
    assert not (tmp_path / "e.jsonl").exists()
