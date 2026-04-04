# ADR 013 — Automated Weekly Impact Report

**Status:** Accepted  
**Date:** 2026-04-03

## Context

SMB clients paying a monthly retainer can lose sight of the agent's value between incidents. A client who hasn't seen a fraud alert in two weeks may assume the agent is idle rather than correctly handling low-volume weeks. The dashboard is available but requires the client to actively visit it. A push-based weekly summary sent directly to their Slack channel creates a recurring proof-of-value touchpoint that requires zero client action.

## Decision

Add a `weekly_report` asyncio scheduler task that sends a Slack message every Monday at ~8:00 AM UTC:

- Queries `AuditLog` (7-day window): total events, success rate, avg processing time, fulfillment holds applied, top exception type
- Calculates estimated hours saved (total × 8 min / 60)
- Delivers via the existing `SlackClient.send_alert()` to `SLACK_DEFAULT_CHANNEL`
- Guards against duplicate sends using a Redis key `agent:weekly_report:last_sent` (25h TTL)
- Runs as a separate `asyncio.create_task` in lifespan; cancelled cleanly on shutdown

**Report format (Slack):**
```
📊 Order Exception Agent — Weekly Impact Report
Mar 24 – Mar 31

• 45 order exceptions handled automatically
• 7.5h of manual triage saved (@ 8 min/event)
• 12 fulfillment holds applied (fraud, address, payment)
• 97.8% success rate | 1 in dead-letter queue
• Most common exception: Fraud Risk
• Avg processing time: 2.3s per event
```

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Email via SendGrid | Adds external dependency; Slack is already integrated |
| Cron job (separate process) | Adds deployment complexity; asyncio task uses existing infrastructure |
| Client self-service (dashboard only) | Requires active engagement; passive push is better for retention |
| Real-time digest (daily) | Daily noise; weekly balances visibility with attention cost |

## Consequences

- **Positive:** Clients receive a concrete "proof of work" every Monday without operator effort
- **Positive:** Redis idempotency key prevents duplicate sends on service restart during Monday morning
- **Positive:** Uses existing SlackClient and DB session factory — zero new dependencies
- **Negative:** Report is always scoped to 7 days from current time, not a clean Mon–Sun week boundary
- **Negative:** Slack webhook must be valid; a misconfigured webhook silently drops the report (logged, not fatal)
