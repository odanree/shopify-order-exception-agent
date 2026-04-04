# ADR-002: Redis SET NX for Webhook Idempotency

**Status**: Accepted  
**Date**: 2026-04-03

## Context

Shopify guarantees at-least-once webhook delivery. Under network failures or Shopify
service hiccups, the same webhook can be delivered 2–5 times within minutes. Each
delivery carries a stable `X-Shopify-Webhook-Id` header. Without deduplication, each
duplicate would trigger a redundant LLM call, duplicate Slack notification, and
duplicate order tags.

## Decision

Use Redis `SET key value NX EX {ttl}` (SET if Not eXists with expiry) as the
idempotency gate in `IdempotencyService.mark_processed`. The key is the raw
`X-Shopify-Webhook-Id`. If the key already exists, the endpoint returns
`{"status": "duplicate"}` without enqueuing a background task.

TTL is set to 604800 seconds (7 days) to cover Shopify's full retry window (up to 48
hours) with ample margin.

## Consequences

**Positive**:
- `SET NX` is atomic — no race condition is possible even under concurrent duplicate
  deliveries hitting multiple app instances simultaneously.
- Redis expiry eliminates the need for a background cleanup job.
- `IdempotencyService` is a thin wrapper with no business logic, straightforwardly
  testable with `fakeredis`.

**Negative**:
- If Redis is unavailable, the check fails open: the webhook is processed to avoid
  blocking legitimate traffic. This could cause a duplicate action during a Redis
  outage. Failing closed is the safer alternative for correctness but creates a
  backlog in Shopify's retry queue. Failing open is the chosen default because
  re-tagging an order with the same exception tag is idempotent.
- Webhooks replayed more than 7 days later will be reprocessed. This is acceptable
  since Shopify's retry window is 48 hours.
