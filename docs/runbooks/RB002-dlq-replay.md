# RB002 — Dead Letter Queue (DLQ) Replay

**Last updated:** 2026-04-08  
**Area:** Operations / incident response

## Purpose

When a webhook fails processing (graph error, Shopify API timeout, etc.) it lands in
the dead letter queue. This runbook covers how to inspect, retry, and resolve DLQ events.

---

## Environment variables

See `docs/runbooks/env.md` (gitignored) for real values.

| Variable | Description |
|----------|-------------|
| `$PROD_HOST` | Production server hostname or IP |
| `$PROD_USER` | SSH user (e.g. `root`) |
| `$SERVICE_URL` | Public HTTPS URL of the agent |
| `$DB_USER` | PostgreSQL app user |
| `$DB_NAME` | PostgreSQL database name |
| `$ORDER_AGENT_CONTAINER` | Docker container name |
| `$POSTGRES_CONTAINER` | Docker postgres container name |

---

## When to use this

- Dashboard shows DLQ events in the **Dead Letter Queue** panel
- An order was received but no audit record was created
- Slack alert fired but no routing action was taken
- Shopify webhook retry is delivering duplicates you want to suppress

---

## Step 1 — List DLQ events

**Via dashboard:**
```
$SERVICE_URL/dashboard
```
Scroll to the **Dead Letter Queue** panel. Each row shows order ID, error, retry count, and queued time.

**Via API:**
```bash
curl -s $SERVICE_URL/api/dlq | python -m json.tool
```

Response fields:
- `event_id` — use this for retry/resolve
- `order_id` — the Shopify order
- `error` — why it failed
- `retry_count` — how many times retried
- `raw_payload` — the original webhook body

---

## Step 2 — Diagnose the failure

```bash
ssh $PROD_USER@$PROD_HOST "docker logs $ORDER_AGENT_CONTAINER --tail 100 2>&1 | grep -A10 '<order_id>'"
```

Common failure causes:
- **Shopify API 401** — access token expired or wrong store domain
- **Graph error** — LLM call failed (rate limit, bad prompt)
- **DB error** — connection pool exhausted or schema mismatch
- **Redis error** — checkpointer unavailable

---

## Step 3 — Retry an event

**Via dashboard:** Click the **Retry** button on the event row.

**Via API:**
```bash
curl -s -X POST $SERVICE_URL/api/dlq/<event_id>/retry | python -m json.tool
```

The retry re-injects the original webhook payload into the graph with a fresh run ID.
On success, the DLQ event is marked resolved and a new audit record is created.

**Note:** If the root cause hasn't been fixed (e.g. Shopify API still returning 401),
the retry will fail again and increment `retry_count`.

---

## Step 4 — Resolve without retrying

If the event should be discarded (e.g. test order, duplicate, no action needed):

**Via dashboard:** Click the **Resolve** button.

**Via API:**
```bash
curl -s -X POST $SERVICE_URL/api/dlq/<event_id>/resolve | python -m json.tool
```

This sets `resolved_at` without reprocessing. The event disappears from the dashboard panel.

---

## Step 5 — Verify resolution

```bash
curl -s $SERVICE_URL/api/dlq | python -m json.tool
# Event should no longer appear

# If retried, verify audit record was created
ssh $PROD_USER@$PROD_HOST "docker exec $POSTGRES_CONTAINER psql -U $DB_USER -d $DB_NAME \
  -c \"SELECT order_id, routing_type, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 3;\""
```

---

## Bulk resolve (emergency)

If the DLQ is flooded with events from a bad deploy or misconfiguration:

```bash
# Resolve all unresolved events (use with caution)
ssh $PROD_USER@$PROD_HOST "docker exec $POSTGRES_CONTAINER psql -U $DB_USER -d $DB_NAME \
  -c \"UPDATE dead_letter_events SET resolved_at = NOW() WHERE resolved_at IS NULL;\""
```

Confirm the root cause is fixed before bulk resolving, or the same orders will re-fail
if Shopify retries the webhooks.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Retry keeps failing | Root cause not fixed | Fix underlying issue first |
| Event not appearing in list | Already resolved | Check with `WHERE resolved_at IS NOT NULL` |
| Dashboard DLQ panel empty but API returns events | UI filter | Reload dashboard |
| High retry count | Repeated auto-retries from Shopify | Resolve to stop the loop, fix root cause |
