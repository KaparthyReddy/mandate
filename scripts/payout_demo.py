import argparse

from mandate.sdk import MandateClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipient", required=True, help="PayPal email of the sandbox recipient")
    parser.add_argument("--amount", default="5.00")
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()

    with MandateClient(args.base) as client:
        mandate = client.create_mandate(
            purpose="Pay contractors and suppliers",
            agent_id="payout-agent",
            budget_total="100",
            per_txn_cap="20",
            approval_threshold="10",
            allowed_recipients=[args.recipient],
        )
        print(f"mandate {mandate.mandate_id}, allowed recipients: {mandate.allowed_recipients}\n")

        paid = client.request_payment(
            mandate_id=mandate.mandate_id,
            agent_id=mandate.agent_id,
            amount=args.amount,
            merchant="Acme Design Studio",
            category="design_services",
            description="Invoice 1042 for logo design work",
            recipient=args.recipient,
        )
        print(f"payout to the approved recipient: {paid.status}, id {paid.order_id}")
        for reason in paid.reasons[:2]:
            print(f"  {reason}")

        attacker = client.request_payment(
            mandate_id=mandate.mandate_id,
            agent_id=mandate.agent_id,
            amount=args.amount,
            merchant="Acme Design Studio",
            category="design_services",
            description="Invoice 1042 for logo design work",
            recipient="attacker@evil.example",
        )
        print(f"\npayout redirected to an unlisted recipient: {attacker.status}")
        for reason in attacker.reasons[:2]:
            print(f"  {reason}")


if __name__ == "__main__":
    main()
