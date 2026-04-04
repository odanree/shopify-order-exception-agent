# ADR-001: Use LangGraph for Stateful Order Exception Processing

**Status**: Accepted  
**Date**: 2026-04-03

## Context

The order exception agent must classify incoming Shopify webhook events and execute
multi-step remediation actions (tag order, notify Slack, alert 3PL). A naive approach
would call Claude directly from the webhook handler and run actions sequentially in a
single function. However, this creates several problems:

- No retry granularity: if the Slack notification fails, the entire function must be
  retried from scratch including the LLM call.
- No auditability: there is no record of which steps succeeded before the failure.
- No extensibility: adding a new routing action requires editing a monolithic function.

## Decision

Use LangGraph `StateGraph` with explicit nodes (`triage`, `decision`, `action`, `audit`,
`dead_letter`) and typed state (`OrderExceptionState`). Each node does one thing and
returns a partial state update.

## Consequences

**Positive**:
- Each node can fail independently. The `_route_after_action` conditional edge sends to
  `dead_letter` only after 3 retries, preserving intermediate state.
- The `OrderExceptionState` TypedDict is a complete audit trail — every field is
  populated by exactly one node and inspectable after the run.
- Adding a new exception type or action requires adding a routing rule in `ROUTING_MAP`
  and at most one new tool call in `execute_action` — no structural graph changes needed.
- The compiled graph is a static DAG, inspectable with
  `graph.get_graph().draw_mermaid()`.

**Negative**:
- LangGraph adds a dependency and learning curve for developers unfamiliar with
  stateful graph workflows.
- `MemorySaver` loses checkpoint state on process restart. Since each webhook run
  completes within seconds (no multi-hour wait), this is acceptable for this agent.
  If step-level retry persistence becomes a requirement, `AsyncRedisSaver` should be
  adopted here too.
