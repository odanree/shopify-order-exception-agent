# ADR-003: Return HTTP 200 Immediately and Process in Background

**Status**: Accepted  
**Date**: 2026-04-03

## Context

Shopify requires webhook endpoints to respond with HTTP 200 within 5 seconds. The full
order exception workflow — LLM classification, Shopify order tagging, Slack notification,
and optionally 3PL notification — can take 3–15 seconds depending on external API
latency. If the endpoint times out, Shopify marks the delivery as failed and re-queues
it for retry, consuming the idempotency key against a half-processed request.

## Decision

Return `{"status": "accepted"}` with HTTP 200 immediately after:
1. Validating the HMAC signature.
2. Checking and setting the idempotency key.
3. Enqueuing the agent run as a FastAPI `BackgroundTask`.

The `BackgroundTask` runs the full LangGraph graph after the HTTP response is sent.

## Consequences

**Positive**:
- Shopify's webhook delivery is acknowledged within milliseconds, preventing re-queues
  and retry storms.
- The 5-second deadline is never at risk even during LLM cold starts.
- The idempotency key is set before the background task starts, so a duplicate delivery
  arriving milliseconds later is correctly deduplicated.

**Negative**:
- Shopify has no visibility into whether background processing succeeded. Failures are
  only observable via logs, the dead-letter queue, and the audit table.
- If the FastAPI worker process crashes after returning 200 but before the background
  task completes, the event is lost. This is a known limitation of in-process background
  tasks versus a durable queue (Celery, ARQ). For current scale this is acceptable. If
  reliability requirements increase, this decision should be revisited in favour of a
  persistent task queue.
