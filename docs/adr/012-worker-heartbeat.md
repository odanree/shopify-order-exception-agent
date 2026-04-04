# ADR 012 — Notification Worker Heartbeat

**Status:** Accepted  
**Date:** 2026-04-03

## Context

The `NotificationWorker` runs as an asyncio background task inside the FastAPI process. It is responsible for delivering Slack alerts and 3PL notifications. If this task stalls — due to a Redis subscribe drop, an unhandled exception in the crash-retry loop, or an asyncio scheduling issue — the web server continues returning 200s for every incoming webhook. The agent still processes orders and writes audit records, but no Slack alerts or 3PL notifications are delivered. This failure mode is silent: there are no 5xx responses and no obvious log errors. An operator would only notice when a client asks why they stopped receiving alerts.

## Decision

Add a heartbeat loop to `NotificationWorker` that runs concurrently with the subscribe loop via `asyncio.gather`:

- `_heartbeat_loop()` pulses `agent:heartbeat:order-exception-worker` Redis key every **5 minutes** with a **700-second TTL**
- TTL is set longer than the interval so the key survives a single missed pulse without triggering a false alert
- `GET /health` reads the key and reports `worker: ok` if age < 600s, `worker: stale:{age}s` if older, `worker: not_started` if absent
- When `/health` detects a degraded worker, it calls `sentry_sdk.capture_message()` at `critical` level
- `/health` returns HTTP 503 when any check is not `"ok"` — Docker Compose and load balancers can gate on this

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Separate watchdog process | Adds process management complexity; asyncio heartbeat is simpler and sufficient |
| Log-based alerting (search for missing log lines) | Requires external log aggregation; async, not real-time |
| HTTP callback from worker to monitoring endpoint | Coupling; worker should not know about monitoring infra |
| Prometheus gauge | Correct long-term, but adds metrics stack dependency not present yet |

## Consequences

- **Positive:** Silent worker stalls are surfaced within 10 minutes via Sentry critical alert
- **Positive:** `/health` returns 503 when degraded, enabling Docker health-based restart policies
- **Positive:** Zero performance impact — Redis SET every 5 minutes is negligible
- **Negative:** If Redis itself is down, the heartbeat cannot pulse and the health check will report `worker: not_started` even if the worker is running — tolerable since Redis down is its own incident
