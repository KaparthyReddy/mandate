from mandate.sdk import MandateClient

with MandateClient("http://localhost:8000") as client:
    mandate = client.create_mandate(
        purpose="Office supplies for the engineering team",
        agent_id="office-agent",
        budget_total="300",
        per_txn_cap="80",
        approval_threshold="40",
    )
    result = client.request_payment(
        mandate_id=mandate.mandate_id,
        agent_id=mandate.agent_id,
        amount="25.00",
        merchant="Staples",
        category="office_supplies",
        description="Printer paper",
    )
    print(result.status, result.reasons)
    if result.approved:
        print("Finish checkout at", result.approval_url)
