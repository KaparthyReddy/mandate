# Mandate

**OAuth for AI spending.** An open-source safety layer that lets AI agents pay through PayPal without being able to spend more, or on other things, than a person allowed.

Built for the PayPal AI Hackathon.

## The problem

Agents can now browse, decide and buy. They also read untrusted text: product pages, emails, invoices, reviews. A single hidden sentence in that text can turn a shopping agent into a thief. Giving an agent a payment tool with no guardrails is the real blocker for agentic commerce.

## What Mandate does

You give an agent a **signed mandate**: a purpose, a budget, a per-payment cap, an approval threshold, and optionally allowed categories, merchants and recipients. Every payment the agent attempts goes through Mandate first.

1. **Deterministic limits** check the signature, expiry, budget, caps, categories, merchants, recipients and agent identity.
2. A **Guardian Council** of three agents then reviews the request in parallel and the strictest vote wins:
   - **Intent Agent** (LLM): does this purchase serve the mandate's purpose?
   - **Injection Sentinel** (rules plus LLM): does anything the agent read try to steer it?
   - **Risk Agent** (no LLM): merchant reputation, typosquats, and spending anomalies such as structuring and probing.
3. Approved payments run through **PayPal**: Orders for checkout purchases, Payouts for paying vendors directly with no checkout click.
4. Payments above the threshold wait for a human in the dashboard.
5. Every step lands in a **hash-chained ledger** that can be verified end to end.

## Results

**MandateBench** simulates a fully compromised agent. A bad payment is an attack that succeeds only if money is actually paid. These are the frozen test split numbers: 150 attacks across 10 classes and 100 honest purchase requests, detectors frozen before the run.

| Configuration | Attacks that succeeded | Honest paid | Honest held for a human | Honest blocked |
|---|---|---|---|---|
| No protection | 100% | 100% | 0% | 0% |
| Limits only | 79.3% | 91% | 9% | 0% |
| Risk + Sentinel (rules, no LLM) | 20.7% | 91% | 9% | 0% |
| **Full council** | **5.3%** | 89% | 9% | 2% |

Held payments are above the approval threshold by design. With 150 attacks, 5.3% has a 95% interval of roughly 2.5% to 10%.

**With a real LLM agent**, Qwen 2.5 7B shopping a store with poisoned listings is hijacked in all five attacks and loses $792. Behind Mandate it loses $0 and completes all three honest purchases.

What still gets through, in plain terms: inflated totals at a vendor you already approve, and a few payee redirects and typosquats. See [the threat model](docs/threat-model.md). The benchmark is synthetic and written by the same authors as the detectors, so expect real-world numbers to differ.

## Run it

**With Docker, no accounts needed.** This uses Postgres, a fake PayPal provider, and the rules-only council:

```bash
docker compose up --build
```

Watch the log for the line `Generated the admin key: ...`, then open http://localhost:8000 and paste it. The key is stored in a volume. To read it again later:

```bash
docker compose exec api cat /data/secrets.json
```

**With the LLM agents** (downloads about 5 GB the first time; wait for the `ollama-pull` service to finish):

```bash
LLM_ENABLED=true docker compose --profile llm up --build
```

**With the real PayPal sandbox:**

```bash
PAYMENT_PROVIDER=paypal PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... docker compose up --build
```

For payouts, enable the **Payouts** feature on your sandbox app. For webhooks, register one in the PayPal dashboard and set `PAYPAL_WEBHOOK_ID`. Unverified webhooks are always rejected.

**Locally without Docker** (Python 3.11 or newer):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
MANDATE_AUTO_SECRETS=true MANDATE_DATA_DIR=./data PAYMENT_PROVIDER=fake LLM_ENABLED=false \
  python -m uvicorn mandate.main:app
```

For LLM mode locally, install [Ollama](https://ollama.com), run `ollama pull qwen2.5:7b-instruct`, and set `OLLAMA_MODEL=qwen2.5:7b-instruct` and `LLM_ENABLED=true`. Use a single model for every agent. Switching models between agents makes Ollama reload on each payment and slows everything down.

## Use it from an agent

**Python SDK:**

```python
from mandate.sdk import MandateClient, admin_key_from_env

with MandateClient("http://localhost:8000", api_key=admin_key_from_env()) as client:
    mandate = client.create_mandate(
        purpose="Office supplies for the engineering team",
        agent_id="office-agent",
        budget_total="300",
        per_txn_cap="80",
        approval_threshold="40",
    )
    agent_key = client.create_agent_key("office-agent")

with MandateClient("http://localhost:8000", api_key=agent_key) as agent:
    result = agent.request_payment(
        mandate_id=mandate.mandate_id,
        agent_id="office-agent",
        amount="25.00",
        merchant="PaperTrail",
        category="office_supplies",
        description="Printer paper",
        evidence="<the product page text the agent read>",
    )
    print(result.status, result.reasons)
```

Always pass the untrusted text the agent read as `evidence`. That is what lets the Sentinel see an injection.

**MCP server**, for any MCP-capable agent. Give it an agent key, never the admin key:

```bash
MANDATE_API_URL=http://localhost:8000 MANDATE_API_KEY=<agent key> MANDATE_MANDATE_ID=<mandate id> mandate-mcp
```

It exposes three tools: `request_payment`, `check_budget` and `payment_status`.

**Paying vendors directly:** list PayPal emails in `allowed_recipients` on the mandate and pass `recipient=` on the request. A payout to anyone else is denied, and the list is signed so it cannot be edited after the fact.

## Try the attacks yourself

```bash
python scripts/agent_demo.py --agent gullible --quiet   # a fully compromised agent, with and without Mandate
python scripts/agent_demo.py --quiet                    # a real LLM agent (needs Ollama)
python scripts/demo_scenarios.py                        # six scenarios against the live council
python scripts/payout_demo.py --recipient <sandbox paypal email>
```

## Reproduce the benchmark

```bash
python scripts/bench.py --split test --per-class 15 --honest-scale 0.5    # rules only, about 30 seconds
python scripts/bench.py --split test --llm --per-class 15 --honest-scale 0.5   # with the LLM agents (about 40 minutes)
```

The dev split is for tuning and the test split is for reporting. Cases are generated from a fixed seed, so the same flags always give the same cases.

## Security

Every endpoint except `/health` and the dashboard page needs an API key. Admin keys do everything. Agent keys are bound to one agent, stored hashed, revocable, rate limited, and can see only their own mandates and payments. Read the [threat model](docs/threat-model.md) for the full list of controls and the residual risks.

## Layout

| Path | What it holds |
|---|---|
| `src/mandate/mandates.py`, `policy.py` | Signed mandates and the deterministic limits |
| `src/mandate/council.py`, `intent_agent.py`, `sentinel.py`, `risk_agent.py` | The Guardian Council |
| `src/mandate/service.py`, `provider.py` | The payment pipeline and PayPal Orders and Payouts |
| `src/mandate/ledger.py` | The hash-chained ledger |
| `src/mandate/auth.py`, `keys.py`, `webhooks.py` | Keys, roles, rate limits and webhook verification |
| `src/mandate/sdk.py`, `mcp_server.py` | Python SDK and MCP server |
| `src/mandate/lab/` | The shopping-agent lab with poisoned listings |
| `src/mandate/bench/` | MandateBench |
| `src/mandate/static/index.html` | The dashboard |
| `docs/` | Threat model and the submission write-up |

## Develop

```bash
ruff check . && ruff format --check . && mypy src tests scripts examples && pytest -q
```

## License

MIT
