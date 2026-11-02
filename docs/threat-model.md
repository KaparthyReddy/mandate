# Mandate threat model

## What Mandate protects

An AI agent that can spend money on a person's behalf. The asset is the person's money and the trust they place in the agent.

## Who we assume is the adversary

1. **A hijacked agent.** The agent follows instructions hidden in product pages, emails, invoices or search results. We treat the agent as fully compromised: anything it submits may be hostile, including the merchant, amount, description and evidence.
2. **A malicious seller or page author.** Writes the text a hijacked agent reads.
3. **A network attacker or script on the same machine.** Calls the Mandate API directly or posts fake PayPal events.

We do **not** defend against a compromised admin key, a compromised Mandate server, or a malicious human operator.

## Trust boundaries

| Component | Trusted? | Notes |
|---|---|---|
| The human and the admin key | Yes | Issues signed mandates, approves held payments, creates agent keys |
| Mandate server and database | Yes | Holds the signing key, the ledger and the policy engine |
| PayPal | Yes | Moves money, signs webhooks |
| The agent and everything it read | **No** | Requests are checked as hostile input |
| Local LLMs (Intent and Sentinel) | Partly | They advise. The deterministic layer decides the hard limits |

## Controls

| Threat | Control | Where |
|---|---|---|
| Agent spends beyond its authority | Signed mandate: budget, per-payment cap, approval threshold, categories, merchants, recipients, expiry | `mandates.py`, `policy.py` |
| Agent or attacker edits a mandate | Ed25519 signature over every field, checked on every payment | `mandates.py` |
| Split purchases to stay under a limit | Budget reserved the moment a payment is approved or held; structuring signal | `service.py`, `anomaly.py` |
| Instruction injection in pages | Rule-based and LLM Sentinel over everything the agent saw | `injection.py`, `sentinel.py` |
| Unrelated purchase | Intent Agent compares the request with the mandate purpose | `intent_agent.py` |
| Known-bad or lookalike merchants | Reputation lookup and edit-distance typosquat check | `risk_agent.py`, `reputation.py` |
| Payee redirection | Optional approved-vendor list and recipient list inside the signed mandate; optional approval of new payees | `policy.py`, `risk_agent.py` |
| Probing by repeated attempts | Repeated-denial signal, rate limit per key | `anomaly.py`, `auth.py` |
| Audit tampering | Hash-chained, verifiable ledger | `ledger.py` |
| Stolen or misused agent key | Keys are bound to one agent, stored hashed, revocable; agents can only see their own mandates and payments | `auth.py`, `keys.py` |
| Fake PayPal events | Signature verification through PayPal's verification API; unverified events are rejected, and an unconfigured server rejects all | `webhooks.py` |
| Duplicate or replayed requests | Idempotent by request id | `service.py` |
| Runaway agent | Kill switch revokes every mandate and rejects pending approvals | `service.py` |
| Component failure | Fail closed: a crashing agent escalates to a human, an unreachable LLM escalates, an unset admin key refuses all requests | `council.py`, `auth.py` |

## Measured results

MandateBench (see `bench_results_test/`): on a frozen test split of 150 attacks and 100 honest purchases, the full council let 5.3% of attacks through and wrongly blocked 2% of honest purchases. Limits alone let 79.3% through.

## Residual risks, stated plainly

- **Inflated totals at an approved vendor, under the approval threshold.** Mandate trusts the amount the agent claims. Only a trusted price source would close this.
- **Instructions in languages or phrasings the rules cannot read.** The LLM Sentinel catches most but not all.
- **LLM judgement is not deterministic proof.** The Intent Agent is uncalibrated: it can be confidently wrong in both directions. It can only escalate or deny, never override the policy limits.
- **Payouts are irreversible.** They are only allowed to recipients on the signed mandate. Keep the list short.
- **Typosquats of short brand names** (under five characters) are skipped, to avoid false positives.
- **The benchmark is synthetic** and written by the same authors as the detectors. Expect real-world rates to differ.
- **No transport security in the demo.** Run behind HTTPS in any real deployment.
