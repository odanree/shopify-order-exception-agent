# Shopify Order Exception Agent

An AI-powered LangGraph agent that monitors a Shopify store for order exceptions, classifies them using Claude, and routes them to the appropriate resolution path — with full observability and a human-in-the-loop kill switch.

**Live demo:** `https://shopify-order.danhle.net/dashboard`

---

## What it does

When a Shopify order is created, the agent:

1. **Receives** the `orders/create` webhook
2. **Classifies** the exception type using Claude (`claude-sonnet-4-6`) — fraud risk, address invalid, payment issue, fulfillment delay, high-value manual review, or no action
3. **Routes** to one of five paths: tag only, tag + Slack alert, tag + Slack + 3PL notification, escalate to manager, or require manual review
4. **Acts** on Shopify — adds tags, places fulfillment holds (via Shopify's fulfillment orders API)
5. **Notifies** via Slack with structured alerts
6. **Audits** every decision to PostgreSQL with LLM token cost tracking

Shadow mode runs the full pipeline without touching real orders — useful for testing and demos.

---

## Architecture

```
Shopify webhook
    │
    ▼
POST /api/webhooks/orders/create
    │  (returns 200 immediately)
    ▼
Background task
    │
    ▼
┌─────────────────────────────────────────┐
│           LangGraph State Machine        │
│                                          │
│  triage_event  ──►  review_order  ──►   │
│  execute_action  ──►  verify  ──►        │
│  record_audit                            │
│                                          │
│  (retry loop on verify failure)          │
│  (dead letter queue on max retries)      │
└─────────────────────────────────────────┘
    │
    ├──► Shopify API  (tags, fulfillment holds)
    ├──► Slack        (via Redis pub/sub event router)
    └──► PostgreSQL   (audit log, DLQ, token costs)
```

**Key design decisions:**
- **Immediate 200** — webhook endpoint returns instantly; all processing is async (ADR 003)
- **Redis idempotency** — duplicate webhooks are deduplicated by `X-Shopify-Webhook-Id` (ADR 002)
- **Pub/sub event router** — Slack notifications are decoupled via Redis channels so the graph never blocks on delivery (ADR 005)
- **Kill switch** — a Redis flag can halt all Shopify mutations instantly without redeploying (ADR 006)
- **Shadow mode** — `AGENT_MODE=shadow` runs the full graph but skips all Shopify writes (ADR 009)
- **LLM cost tracking** — every triage call captures token usage and USD cost to PostgreSQL (ADR 014)

---

## Tech stack

| Layer | Technology |
|-------|------------|
| Agent framework | LangGraph (stateful graph, retry loops) |
| LLM | Claude `claude-sonnet-4-6` via Anthropic API |
| API | FastAPI (async, background tasks) |
| Database | PostgreSQL + SQLAlchemy (async) + Alembic |
| Cache / idempotency | Redis |
| Notifications | Slack Incoming Webhooks (via pub/sub) |
| Deployment | Docker Compose + Caddy reverse proxy |
| CI | GitHub Actions (ruff + pytest) |

---

## Project structure

```
app/
  agent/
    graph.py       # LangGraph state machine definition
    nodes.py       # Node functions (triage, review, execute, verify, audit)
    state.py       # OrderExceptionState TypedDict
    tools.py       # Shopify + audit tools (injected dependencies)
  routers/
    webhooks.py    # Shopify webhook endpoints
    dashboard.py   # Ops dashboard (HTML + JSON stats API)
    dlq.py         # Dead letter queue management
  services/
    shopify_client.py   # Shopify Admin API wrapper
    slack_client.py     # Slack webhook client
    event_router.py     # Redis pub/sub event router
    idempotency.py      # Webhook deduplication
    kill_switch.py      # Redis-backed kill switch
  models/
    db.py          # SQLAlchemy ORM models
  db/
    session.py     # Async engine + session factory
alembic/           # Database migrations
docs/
  adr/             # Architecture Decision Records (ADR 001–014)
  runbooks/        # Operational runbooks (shadow mode, DLQ replay)
  LEARNINGS.md     # Debugging gotchas and lessons learned
scripts/
  seed_demo.py         # Send demo webhook payloads
  register_webhooks.py # Register webhooks with Shopify
tests/
  test_agent.py    # Node-level unit tests
  test_webhooks.py # Webhook endpoint tests
```

---

## Running locally

```bash
# Install dependencies
pip install -e ".[dev]"

# Start infrastructure
docker compose up -d postgres redis

# Run migrations
alembic upgrade head

# Start the API
uvicorn app.main:app --reload

# In another terminal, seed demo events
python scripts/seed_demo.py
```

Environment variables (see `.env.example`):

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL async URL |
| `REDIS_URL` | Redis connection URL |
| `SHOPIFY_SHOP_DOMAIN` | e.g. `yourstore.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Shopify Admin API token |
| `SHOPIFY_WEBHOOK_SECRET` | HMAC secret for webhook verification |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `AGENT_MODE` | `shadow` to skip Shopify mutations |
| `ADMIN_API_KEY` | Dashboard HTTP Basic Auth password |

---

## Dashboard

The ops dashboard at `/dashboard` shows:
- Events processed (7d)
- LLM cost and token breakdown (7d)
- Action breakdown by routing type
- Dead letter queue with retry/resolve buttons

Protected by HTTP Basic Auth when `ADMIN_API_KEY` is set.

---

## Documentation

- [ADR 001–014](docs/adr/) — Architecture decisions
- [LEARNINGS.md](docs/LEARNINGS.md) — Debugging lessons
- [Runbooks](docs/runbooks/) — Operational procedures
