"""Tests for the LangGraph agent nodes."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.state import OrderExceptionState


def _make_state(**overrides) -> OrderExceptionState:
    base: OrderExceptionState = {
        "webhook_id": "test-webhook-001",
        "event_type": "orders/create",
        "order_id": "12345",
        "raw_payload": {
            "id": 12345,
            "total_price": "50.00",
            "financial_status": "paid",
            "fulfillment_status": None,
            "risk_level": "LOW",
            "tags": "",
            "shipping_address": {"country": "US", "address1": "123 Main", "city": "NYC", "zip": "10001"},
        },
        "exception_type": None,
        "routing_decision": None,
        "verification_passed": None,
        "tool_calls_log": [],
        "llm_input_tokens": None,
        "llm_output_tokens": None,
        "error": None,
        "retry_count": 0,
        "processing_start_ms": 0,
        "messages": [],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_decision_node_maps_fraud_risk():
    """fraud_risk exception type should route to tag_slack_and_3pl."""
    from app.agent.nodes import make_decision

    state = _make_state(exception_type="fraud_risk")
    result = await make_decision(state)
    assert result["routing_decision"] == "tag_slack_and_3pl"


@pytest.mark.asyncio
async def test_decision_node_maps_address_invalid():
    from app.agent.nodes import make_decision

    state = _make_state(exception_type="address_invalid")
    result = await make_decision(state)
    assert result["routing_decision"] == "tag_and_slack"


@pytest.mark.asyncio
async def test_decision_node_maps_high_value():
    from app.agent.nodes import make_decision

    state = _make_state(exception_type="high_value")
    result = await make_decision(state)
    assert result["routing_decision"] == "tag_and_slack"


@pytest.mark.asyncio
async def test_decision_node_maps_unknown():
    from app.agent.nodes import make_decision

    state = _make_state(exception_type="unknown")
    result = await make_decision(state)
    assert result["routing_decision"] == "tag_only"


@pytest.mark.asyncio
async def test_decision_node_maps_payment_issue():
    from app.agent.nodes import make_decision

    state = _make_state(exception_type="payment_issue")
    result = await make_decision(state)
    assert result["routing_decision"] == "escalate"


@pytest.mark.asyncio
async def test_execute_action_logs_tool_calls():
    """execute_action should populate tool_calls_log on success."""
    from app.agent import nodes
    from app.agent import tools

    mock_shopify = AsyncMock()
    # The tool wrapper returns {"success": True, "data": ...} — mock at that level
    mock_shopify.update_order_tags = AsyncMock(
        return_value={"add": {"node": {"id": "gid://shopify/Order/12345", "tags": ["exception:unknown"]}}}
    )

    tools._shopify_client = mock_shopify
    tools._threpl_client = AsyncMock()
    tools._threpl_client.notify_order_exception = AsyncMock(return_value=True)

    state = _make_state(exception_type="unknown", routing_decision="tag_only")
    result = await nodes.execute_action(state)

    assert result["error"] is None
    assert len(result["tool_calls_log"]) >= 1


@pytest.mark.asyncio
async def test_route_to_audit_when_verified():
    """verification_passed=True should route to audit."""
    from app.agent.graph import _route_after_verify

    state = _make_state(verification_passed=True)
    route = _route_after_verify(state)
    assert route == "audit"


@pytest.mark.asyncio
async def test_route_retry_when_verify_fails():
    """verification_passed=False with retries remaining should cycle back to action."""
    from app.agent.graph import _route_after_verify

    state = _make_state(verification_passed=False, retry_count=0)
    route = _route_after_verify(state)
    assert route == "action"


@pytest.mark.asyncio
async def test_dead_letter_after_verify_max_retries():
    """verification_passed=False at retry_count >= 2 should route to dead_letter."""
    from app.agent.graph import _route_after_verify

    state = _make_state(verification_passed=False, retry_count=2)
    route = _route_after_verify(state)
    assert route == "dead_letter"


@pytest.mark.asyncio
async def test_full_graph_triage_to_audit():
    """End-to-end graph run: triage -> decision -> action -> verify -> audit.

    All Shopify and DB calls are mocked; the LLM returns a fixed classification.
    Verifies the graph completes without error and audit is recorded.
    """
    from app.agent import tools
    from app.agent.graph import process_webhook_event

    # Mock Shopify client
    mock_shopify = AsyncMock()
    mock_shopify.update_order_tags = AsyncMock(
        return_value={"add": {"node": {"id": "gid://shopify/Order/99999", "tags": ["exception:fraud_risk"]}}}
    )
    mock_shopify.get_order = AsyncMock(return_value={"tags": ["exception:fraud-risk"]})
    mock_shopify.place_fulfillment_hold = AsyncMock(return_value={"success": True})
    tools._shopify_client = mock_shopify
    tools._threpl_client = AsyncMock()
    tools._threpl_client.notify_order_exception = AsyncMock(return_value=True)

    # Mock DB factory
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_db = MagicMock(return_value=mock_session)
    tools._db_factory = mock_db

    with patch("app.agent.nodes._get_llm") as mock_llm_factory:
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "fraud_risk"
        mock_response.usage_metadata = {"input_tokens": 150, "output_tokens": 5}
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.return_value = mock_llm

        state = _make_state(
            webhook_id="e2e-test-001",
            order_id="99999",
            raw_payload={
                "id": 99999,
                "total_price": "350.00",
                "financial_status": "paid",
                "fulfillment_status": None,
                "risk_level": "HIGH",
                "tags": "",
                "shipping_address": {
                    "country": "NG", "address1": "Unknown", "city": "Lagos", "zip": "00100"
                },
            },
        )

        result = await process_webhook_event(state)

    assert result["exception_type"] == "fraud_risk"
    assert result["routing_decision"] == "tag_slack_and_3pl"
    assert result["verification_passed"] is True
    assert result["error"] is None
    assert result["llm_input_tokens"] == 150
