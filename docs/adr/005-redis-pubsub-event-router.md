# ADR 005 — Redis Pub/Sub Event Router for Slack Notifications

**Status:** Accepted  
**Date:** 2026-04-03

## Context

The `create_slack_alert` tool was in `ALL_TOOLS` — the list passed to the LLM. This had two problems:

1. **Context pollution:** Slack delivery success/failure responses appeared in the LLM's message history, adding noise the model does not need for order triage decisions.
2. **Coupling:** A Slack API failure would surface as a tool-call error inside the agent, potentially affecting retry logic and audit records for what is fundamentally an informational side-effect.

The clawhip architecture (claw-code post, April 2026) articulates this pattern: notifications belong outside the agent context window. The agent should emit structured events to a broker; a separate daemon handles delivery.

## Decision

Implement a Redis pub/sub event router:

- `EventRouter.emit(event_type, payload)` — called from agent nodes (not an LLM tool), publishes a JSON event to the `shopify:events:order-notifications` channel.
- `NotificationWorker` — an asyncio background task started at app startup. Subscribes to the channel, dispatches `order_exception_alert` events to `SlackClient.send_alert()`.
- `create_slack_alert` is removed from `ALL_TOOLS` and from `tools.py` entirely.
- `execute_action` calls `_event_router.emit()` directly; `_event_router` is injected at startup via `inject_event_router()` in `nodes.py`.

The worker runs in the same process as the FastAPI app (same event loop). A separate process is not needed for MVP.

## Consequences

**Positive:**
- Slack delivery is entirely outside the LLM context window.
- Slack failures do not affect agent retry counts or dead-letter routing.
- `ALL_TOOLS` is reduced from 5 to 4 tools, keeping the tool list lean.
- The pattern extends naturally to other notification channels (email, PagerDuty) by adding handlers in `NotificationWorker._dispatch()`.

**Negative:**
- The worker requires Redis pub/sub support (not a problem — Redis is already required for checkpointing and rate limiting).
- Events are fire-and-forget: if the worker crashes between the emit and the delivery, the notification is lost. A durable queue (Redis Streams, Celery) would be required for guaranteed delivery.
- The `decode_responses=True` Redis client is reused for pub/sub. The pub/sub connection is a separate TCP connection from the pool, which is fine, but operators should be aware of the connection count.
