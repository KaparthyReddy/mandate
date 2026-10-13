import argparse
import json
import sys
import time
from pathlib import Path

from mandate.bench.cache import CachingLLM
from mandate.bench.generate import generate
from mandate.bench.report import render_markdown, summarize
from mandate.bench.runner import CONFIGS, LLM_CONFIGS, CaseResult, run_config
from mandate.config import get_settings
from mandate.llm import OllamaClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--per-class", type=int, default=30)
    parser.add_argument("--honest-scale", type=float, default=1.0)
    parser.add_argument("--configs", default="")
    parser.add_argument("--llm", action="store_true", help="include the LLM-backed configurations")
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", default="bench_results")
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.ollama_model
    names = [n.strip() for n in args.configs.split(",") if n.strip()] or [
        n for n in CONFIGS if args.llm or n not in LLM_CONFIGS
    ]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        sys.exit(f"unknown configs: {unknown}. choose from {CONFIGS}")

    out = Path(args.out) / f"{args.split}-seed{args.seed}"
    out.mkdir(parents=True, exist_ok=True)
    cases = generate(args.seed, args.split, args.per_class, args.honest_scale)
    (out / "cases.jsonl").write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    print(f"{len(cases)} cases ({args.split}, seed {args.seed}); configs: {', '.join(names)}")

    llm: CachingLLM | None = None
    if any(n in LLM_CONFIGS for n in names):
        client = OllamaClient(settings.ollama_url, model, settings.llm_timeout)
        llm = CachingLLM(client, out / f"llm_cache_{model.replace(':', '-')}.jsonl", salt=model)

    results: dict[str, list[CaseResult]] = {}
    for name in names:
        started = time.perf_counter()

        def progress(done: int, total: int, label: str = name) -> None:
            if done % 25 == 0 or done == total:
                print(f"  {label}: {done}/{total}", flush=True)

        results[name] = run_config(name, cases, llm, out / "work", progress)
        print(f"{name} done in {time.perf_counter() - started:.1f}s")

    summary = summarize(cases, results)
    summary["model"] = model if llm else None
    summary["llm_cache_hits"] = llm.hits if llm else 0
    (out / "results.json").write_text(json.dumps(summary, indent=2))
    report = render_markdown(summary, f"MandateBench {args.split} split (seed {args.seed})")
    (out / "report.md").write_text(report)
    print()
    print(report)


if __name__ == "__main__":
    main()
