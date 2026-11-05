# Demo video script (under 3 minutes)

The rules require a public YouTube video under 3 minutes, in English, with no copyrighted music and no third-party trademarks. Use only the demo merchants (BigMart, FreshFarm, GameZone, CryptoExchange, PaperTrail, DeskWorks). Do not show real brand logos. PayPal's own name is fine.

## Before recording

1. Stop the server, delete the database, restart: `rm -f mandate.db`.
2. Quit Ollama's menu-bar app, then start it kept warm: `OLLAMA_KEEP_ALIVE=1h ollama serve`, and warm the model: `ollama run qwen2.5:7b-instruct "ok" > /dev/null`.
3. Make sure `.env` has `OLLAMA_MODEL=qwen2.5:7b-instruct`.
4. Start the server in one terminal. Open the dashboard in a browser, paste the admin key, and zoom the browser so text is readable.
5. Have a second terminal ready with a large font.

## Timeline

| Time | On screen | Say |
|---|---|---|
| 0:00 to 0:20 | Terminal: `python scripts/agent_demo.py --scenario 4 --mode naive` | "This shopping agent was asked to buy sticky notes. The listing contains a hidden instruction. It follows it and buys a gift card the user never asked for. Ninety dollars gone." |
| 0:20 to 0:45 | Dashboard, issue a mandate: purpose, budget, caps | "Mandate fixes that. You give an agent a signed spending mandate: what it may buy, how much, and when to ask you." |
| 0:45 to 1:25 | Terminal: `python scripts/agent_demo.py --scenario 4 --mode protected`, then the dashboard feed | "Same agent, same poisoned page, now behind Mandate. Three agents review every payment in parallel. The intent agent was fooled, but the injection sentinel caught the hidden instruction. Strictest vote wins. The payment is blocked." |
| 1:25 to 1:50 | Dashboard: click a blocked card to show reasons, point at the ledger strip | "Every decision is written to a hash-chained ledger with each agent's reasoning, and the ledger can be verified." |
| 1:50 to 2:10 | Dashboard: press Large purchase, then Approve in the inbox | "Anything above the approval threshold waits for a human. One click approves it and PayPal creates the order." |
| 2:10 to 2:30 | Terminal: `python scripts/payout_demo.py --recipient <email>` | "Agents can also pay vendors directly through PayPal Payouts, but only to recipients you signed into the mandate. A payout to anyone else is denied." |
| 2:30 to 2:50 | The MandateBench headline table from the README | "We measured it. Against a fully compromised agent: no protection, 100 percent of attacks succeed. Limits only, 79. The full council, 5.3, with 2 percent of honest payments wrongly blocked. We also publish what still gets through." |
| 2:50 to 3:00 | Dashboard, then the repo URL | "Mandate: OAuth for AI spending. Open source, one docker compose up." |

## Tips

- Record the terminals and browser at 1080p and do a dry run first. The first LLM call after a restart is slow, which is why the model is warmed up.
- If a scenario behaves differently from the script, stop and rerun it rather than editing around it. Show the real behavior.
- Upload as public on YouTube, not unlisted, and paste the link into the submission.
