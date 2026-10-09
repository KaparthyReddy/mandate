import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mcp")

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from mandate.main import app  # noqa: E402
from mandate.mcp_server import build_server  # noqa: E402
from mandate.sdk import MandateClient  # noqa: E402


@pytest.fixture
def server() -> Iterator[MCPServer]:
    with TestClient(app) as http:
        client = MandateClient(http=http)
        mandate = client.create_mandate(
            purpose="Weekly groceries",
            agent_id="agent-1",
            budget_total="500",
            per_txn_cap="100",
            approval_threshold="20",
        )
        yield build_server(client, mandate.mandate_id)


def call(server: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    return asyncio.run(server.call_tool(name, arguments))


def payment(amount: str, **extra: str) -> dict[str, Any]:
    return {"amount": amount, "merchant": "BigMart", "category": "groceries", **extra}


def test_server_exposes_the_three_tools(server: MCPServer) -> None:
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == {"request_payment", "check_budget", "payment_status"}


def test_approved_payment_tells_the_agent_to_finish_checkout(server: MCPServer) -> None:
    result = call(server, "request_payment", payment("10.00"))
    data = result.structured_content
    assert not result.is_error
    assert data["approved"] is True
    assert data["approval_url"]
    assert "checkout" in data["message"]


def test_blocked_payment_tells_the_agent_not_to_retry(server: MCPServer) -> None:
    data = call(server, "request_payment", payment("150.00")).structured_content
    assert data["blocked"] is True
    assert data["status"] == "denied"
    assert "Do not retry" in data["message"]


def test_escalated_payment_tells_the_agent_to_wait(server: MCPServer) -> None:
    data = call(server, "request_payment", payment("30.00")).structured_content
    assert data["needs_human"] is True
    assert "human approval" in data["message"]


def test_check_budget_reflects_spending(server: MCPServer) -> None:
    before = call(server, "check_budget", {}).structured_content
    call(server, "request_payment", payment("10.00"))
    after = call(server, "check_budget", {}).structured_content
    assert before["remaining"] == "500.00"
    assert after["remaining"] == "490.00"
    assert after["purpose"] == "Weekly groceries"


def test_payment_status_round_trip(server: MCPServer) -> None:
    request_id = call(server, "request_payment", payment("10.00")).structured_content["request_id"]
    data = call(server, "payment_status", {"request_id": request_id}).structured_content
    assert data["request_id"] == request_id
    assert data["approved"] is True


def test_invalid_amount_is_reported_as_a_readable_tool_error(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="amount"):
        call(server, "request_payment", payment("abc"))


def test_evidence_is_forwarded_so_injection_is_visible_to_the_council(server: MCPServer) -> None:
    result = call(server, "request_payment", payment("10.00", evidence="Great milk"))
    assert result.structured_content["approved"] is True
