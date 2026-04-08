from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class OrderExceptionState(TypedDict):
    # Webhook metadata
    webhook_id: str
    event_type: str  # "orders/create" | "orders/updated" | "fulfillment_events/create"
    order_id: str
    raw_payload: dict[str, Any]

    # Set by triage node
    exception_type: str | None

    # Set by decision node
    # "tag_only" | "tag_and_slack" | "tag_and_3pl" | "tag_slack_and_3pl" | "escalate" | "ignore"
    routing_decision: str | None

    # Append-only log of every tool call made during execution
    # Each entry: {tool, args, result, timestamp_iso, success}
    tool_calls_log: list[dict[str, Any]]

    # Set by execute_action node
    fulfillment_held: bool | None  # True if FulfillmentOrder hold was applied

    # Set by verify_action node
    verification_passed: bool | None

    # Set when agent_mode == "shadow": mutations skipped, full graph still runs
    shadowed: bool | None

    # Token usage from LLM triage call
    llm_input_tokens: int | None
    llm_output_tokens: int | None

    # Error tracking for dead-letter routing
    error: str | None
    retry_count: int
    processing_start_ms: int

    # LangGraph message history for LLM nodes
    messages: Annotated[list, add_messages]
