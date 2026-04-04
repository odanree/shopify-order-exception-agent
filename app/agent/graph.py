"""Build and expose the compiled LangGraph order exception state machine."""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    execute_action,
    handle_dead_letter,
    make_decision,
    record_audit,
    triage_event,
    verify_action,
)
from app.agent.state import OrderExceptionState


def _route_after_verify(state: OrderExceptionState) -> str:
    """Route after verify_action:
    - passed → audit
    - failed + retries remaining → action (cycle back)
    - failed + max retries → dead_letter
    """
    if state.get("verification_passed"):
        return "audit"
    if state.get("retry_count", 0) >= 2:
        return "dead_letter"
    return "action"


def build_graph():
    builder = StateGraph(OrderExceptionState)

    builder.add_node("triage", triage_event)
    builder.add_node("decision", make_decision)
    builder.add_node("action", execute_action)
    builder.add_node("verify", verify_action)
    builder.add_node("audit", record_audit)
    builder.add_node("dead_letter", handle_dead_letter)

    builder.set_entry_point("triage")
    builder.add_edge("triage", "decision")
    builder.add_edge("decision", "action")
    builder.add_edge("action", "verify")
    builder.add_conditional_edges("verify", _route_after_verify)
    builder.add_edge("audit", END)
    builder.add_edge("dead_letter", END)

    return builder.compile(checkpointer=MemorySaver())


graph = build_graph()


async def process_webhook_event(initial_state: OrderExceptionState) -> OrderExceptionState:
    """Run the graph for a single webhook event.

    Each webhook gets a unique thread_id derived from its webhook_id so that
    checkpointing is isolated per event.
    """
    config = {"configurable": {"thread_id": initial_state["webhook_id"]}}
    result = await graph.ainvoke(initial_state, config=config)
    return result
