# ADR 011 — Sentry Error Monitoring

**Status:** Accepted  
**Date:** 2026-04-03

## Context

The agent runs as an asyncio background service processing Shopify webhooks. When a Shopify API change breaks a mutation, a Redis connection drops, or an LLM response causes an unexpected exception, the current setup only surfaces these errors in structlog JSON output. In a hosted deployment, operators would need to shell into the container and tail logs to discover the failure. For a client-facing service, this is unacceptable.

An async error monitoring service is needed that captures exceptions at the point of failure, attaches structured context (order_id, exception_type, retry_count), and pages the operator.

## Decision

Integrate Sentry SDK with FastAPI and SQLAlchemy integrations.

**Initialization:** In `lifespan()`, if `SENTRY_DSN` is set, call `sentry_sdk.init()` with:
- `FastApiIntegration()` — captures unhandled HTTP exceptions with request context
- `SqlalchemyIntegration()` — captures slow/failed queries
- `traces_sample_rate=0.2` — 20% of requests traced (performance monitoring, not free tier exhausting)
- `environment=app_env` — separates staging from production events in Sentry UI

**Explicit captures:**
- `handle_dead_letter` node: `sentry_sdk.capture_message()` at `error` level with order_id, webhook_id, exception_type, retry_count — because dead-letter events represent agent failures that need operator attention

**Graceful degradation:** `sentry_sdk` is wrapped in `try/except ImportError` at all call sites. If the package is not installed, the agent runs normally with no error — Sentry is opt-in via DSN.

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| BetterStack Logs | Good for log ingestion, weaker for exception grouping and deduplication |
| Datadog | Cost and setup complexity disproportionate to a single-service deployment |
| Email alerts only | No structured context; delayed; hard to triage |
| structlog + log aggregator (Loki) | Requires Grafana stack setup; not available in dev/staging |
| Custom webhook to Slack on error | Re-invents Sentry without grouping, rate limiting, or source maps |

## Consequences

- **Positive:** Operators get paged when the agent dead-letters an event — no log tailing required
- **Positive:** FastAPI integration captures request-level context (path, method, headers) automatically
- **Positive:** Zero-cost when DSN is not set — no behavioral change in local dev
- **Positive:** Sentry free tier covers the expected event volume for a single SMB client
- **Negative:** `sentry-sdk[fastapi]` adds a production dependency (~3MB)
- **Negative:** Sentry captures may include PII in the `extras` dict (order_id, webhook payload fragments) — ensure DSN is only configured when data processing agreement covers the Sentry region
