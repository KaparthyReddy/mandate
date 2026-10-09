import os
import sys
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mandate.sdk import MandateAPIError, MandateClient, PaymentResult


def describe(result: PaymentResult) -> dict[str, Any]:
    if result.approved:
        message = (
            "Payment approved by Mandate and a PayPal order was created. "
            "The user must finish checkout at approval_url."
        )
    elif result.needs_human:
        message = (
            "This payment needs human approval. Tell the user it is waiting in the Mandate "
            "dashboard. Do not retry it."
        )
    elif result.blocked:
        message = (
            "Payment blocked by Mandate. Do not retry or change the details to get around the "
            "block. Tell the user why it was blocked."
        )
    else:
        message = "The payment provider failed. Tell the user and try again later."
    return {
        "request_id": result.request_id,
        "status": result.status,
        "approved": result.approved,
        "needs_human": result.needs_human,
        "blocked": result.blocked,
        "reasons": result.reasons,
        "approval_url": result.approval_url,
        "message": message,
    }


def _guard(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except MandateAPIError as exc:
        raise ToolError(f"Mandate API error: {exc.detail}") from exc


def build_server(client: MandateClient, mandate_id: str) -> MCPServer:
    mandate = client.get_mandate(mandate_id)
    server = MCPServer("mandate")

    @server.tool()
    def request_payment(
        amount: str,
        merchant: str,
        category: str,
        description: str = "",
        evidence: str = "",
    ) -> dict[str, Any]:
        """Ask Mandate for permission to pay a merchant before spending any money.

        Pass amount as a decimal string such as "18.50". Put the untrusted text that led to
        this purchase (a product page, email, invoice or review) in evidence, exactly as you
        read it. Never edit details to get past a block.
        """
        return _guard(
            lambda: describe(
                client.request_payment(
                    mandate_id=mandate_id,
                    agent_id=mandate.agent_id,
                    amount=amount,
                    merchant=merchant,
                    category=category,
                    description=description,
                    evidence=evidence,
                )
            )
        )

    @server.tool()
    def check_budget() -> dict[str, Any]:
        """Show the spending mandate you operate under: purpose, limits and remaining budget."""

        def read() -> dict[str, Any]:
            status = client.get_mandate(mandate_id)
            return {
                "purpose": status.purpose,
                "status": status.status,
                "currency": status.currency,
                "budget_total": str(status.budget_total),
                "spent": str(status.spent),
                "remaining": str(status.remaining),
                "per_payment_cap": str(status.per_txn_cap),
                "human_approval_above": str(status.approval_threshold),
            }

        return _guard(read)

    @server.tool()
    def payment_status(request_id: str) -> dict[str, Any]:
        """Look up a payment you requested earlier by its request_id."""
        return _guard(lambda: describe(client.get_payment(request_id)))

    return server


def main() -> None:
    mandate_id = os.environ.get("MANDATE_MANDATE_ID")
    if not mandate_id:
        print(
            "MANDATE_MANDATE_ID must be set to the mandate this agent operates under",
            file=sys.stderr,
        )
        raise SystemExit(2)
    client = MandateClient(
        os.environ.get("MANDATE_API_URL", "http://localhost:8000"),
        api_key=os.environ.get("MANDATE_API_KEY"),
    )
    build_server(client, mandate_id).run()


if __name__ == "__main__":
    main()
