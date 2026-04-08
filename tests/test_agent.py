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
    mock_shopify.update_order_tags = AsyncMock(return_value={"add": {"node": {"id": "gid://shopify/Order/12345"}}})

    tools._shopify_client = mock_shopify
    tools._threpl_client = AsyncMock()
    tools._threpl_client.notify_order_exception = AsyncMock(return_value=True)

    state = _make_state(exception_type="unknown", routing_decision="tag_only")
    result = await nodes.execute_action(state)

    assert result["error"] is None
    assert len(result["tool_calls_log"]) >= 1
    assert result["tool_calls_log"][0]["tool"] == "update_order_tags"


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
