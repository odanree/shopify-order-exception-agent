# ADR 006 — Redis-Backed Kill Switch for Agent Control

**Status:** Accepted  
**Date:** 2026-04-03

## Context

The order exception agent processes Shopify webhooks automatically. During Shopify API incidents, incorrect classifier behaviour, or merchant contract disputes, operators need to halt all automated processing immediately without redeploying or restarting the service. A per-store kill switch reduces blast radius and avoids a single disable that would affect multi-tenant deployments in future.

## Decision

Implement a Redis-backed kill switch checked at the webhook entry point before any background task is dispatched.

- **Redis key:** `agent:enabled:{store_domain}` — absent or `"1"` means enabled; `"0"` means disabled
- **Default when key is absent:** enabled (safe default — new stores are not inadvertently blocked)
- **Admin API:** `POST /api/admin/agent-control` (enable/disable), `GET /api/admin/agent-status` — authenticated via `X-Admin-Key` header when `ADMIN_API_KEY` is set
- **Response when suppressed:** HTTP 200 with `{"status": "accepted", "action": "suppressed_kill_switch"}` — returns 200 so Shopify does not retry suppressed events

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Environment variable / restart | Requires redeploy; too slow in an incident |
| Feature flag service (LaunchDarkly etc.) | Added dependency; overkill for a binary on/off |
| Database row | Redis is already present; DB adds latency and a write dependency during incidents |
| In-memory flag | Lost on restart; cannot be toggled externally without SSH access |

## Consequences

- **Positive:** Operators can halt processing in <1 second via a single HTTP call or Redis CLI command
- **Positive:** Per-store granularity allows surgical disabling without affecting other tenants
- **Positive:** Zero-dependency on the processing stack (check happens before any Shopify calls)
- **Negative:** Redis availability becomes a soft prerequisite for the kill switch to work; if Redis is down the switch defaults to enabled (tolerable — Redis outage is a separate incident)
- **Negative:** Admin API requires out-of-band credential management (`ADMIN_API_KEY`)
