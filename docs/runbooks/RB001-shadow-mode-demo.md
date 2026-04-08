# RB001 — Shadow Mode Demo and Smoke Test

**Last updated:** 2026-04-08  
**Area:** Demo / QA / shadow mode

## Purpose

Validate the full agent pipeline without touching real Shopify orders. Shadow mode
runs the complete graph (classify → review → route → notify → audit) but skips
Shopify mutations (fulfillment holds, tags). Slack messages are prefixed `[SHADOW]`.

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

## Prerequisites

- Agent deployed and reachable at `$SERVICE_URL`
- `AGENT_MODE=shadow` set in the container (verify below)
- `SLACK_WEBHOOK_URL` set so notifications fire
- `pip install httpx` in your local env

---

## Step 1 — Verify shadow mode is active

```bash
ssh $PROD_USER@$PROD_HOST "docker exec $ORDER_AGENT_CONTAINER env | grep AGENT_MODE"
# Expected: AGENT_MODE=shadow
```

If missing, add to `portfolio-infra/docker-compose.yml` under the service's `environment:`
block and redeploy:
```yaml
AGENT_MODE: shadow
```

---

## Step 2 — Run the seed script

```bash
cd shopify-order-exception-agent

# All 6 scenarios
python scripts/seed_demo.py --url $SERVICE_URL

# Single scenario
python scripts/seed_demo.py --url $SERVICE_URL --scenario fraud
```

**Scenarios:**

| Key | Description | Expected routing |
|-----|-------------|-----------------|
| `fraud` | High-risk score, international billing | `tag_slack_and_3pl` or `escalate` |
| `address` | Missing city/zip | `tag_and_slack` |
| `high_value` | $1,450 order (>$500 threshold → manual review) | `require_manual_review` |
| `payment` | payment_pending status | `tag_and_slack` |
| `fulfillment_delay` | Stuck unfulfilled | `tag_and_slack` |
| `unknown` | Minimal payload, fallback test | `no_action` or `tag_and_slack` |

---

## Step 3 — Verify Slack messages

Each scenario that routes to `tag_and_slack`, `tag_slack_and_3pl`, `escalate`, or
`require_manual_review` should produce a `[SHADOW]` prefixed message in the configured
Slack channel within ~5 seconds.

Expected message format:
```
[SHADOW] Order Exception Alert
Order: #1001 | $247.00
Routing: tag_slack_and_3pl
Reason: High fraud risk score...
```

---

## Step 4 — Verify dashboard

```
$SERVICE_URL/dashboard
```

Check:
- **Events Processed (7d)** increments
- **LLM Cost (7d)** shows non-zero cost
- **Action Breakdown** table reflects the routing decisions
- Shadow mode banner is visible at the top

---

## Step 5 — Verify audit records

```bash
ssh $PROD_USER@$PROD_HOST "docker exec $POSTGRES_CONTAINER psql -U $DB_USER -d $DB_NAME \
  -c \"SELECT order_id, routing_type, shadowed, input_tokens, cost_usd FROM audit_logs ORDER BY created_at DESC LIMIT 6;\""
```

Expected: `shadowed = true` on all recent rows, `input_tokens` non-null.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No Slack messages | `SLACK_WEBHOOK_URL` not set or `AGENT_MODE` not shadow | Check env vars |
| Messages not prefixed `[SHADOW]` | `AGENT_MODE` not set to `shadow` | Add env var, restart |
| Seed returns non-200 | Service down or wrong URL | Check `$SERVICE_URL/health` |
| No audit rows | DB session issue or graph error | Check `docker logs $ORDER_AGENT_CONTAINER` |
