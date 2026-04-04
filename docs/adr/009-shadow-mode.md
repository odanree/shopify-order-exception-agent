# ADR 009 — Shadow Mode for Safe Client Onboarding

**Status:** Accepted  
**Date:** 2026-04-03

## Context

Before giving the agent write access to a live Shopify store, clients (and the operator) need a trust-building period where they can observe the agent's decisions without consequences. Running the agent against a dev store is useful but doesn't reflect real order data and traffic patterns. What's needed is the ability to run the full production graph against real webhooks while skipping only the irreversible write mutations.

## Decision

Add `AGENT_MODE=shadow` environment variable (default: `live`). When shadow mode is active:

- The full LangGraph pipeline executes: triage → decision → execute_action → verify → audit
- In `execute_action`, all Shopify write calls (tag updates, fulfillment holds, 3PL notifications) are **skipped**
- The intended action is logged at INFO level with `execute_action_shadowed` key including `would_apply_tag`, `would_hold`, `routing`, and `exception_type`
- `shadowed: True` is recorded in state and persisted in the audit log
- Slack notifications still fire (via event router) — operators can observe in real time what the agent would have done
- The dashboard shows a persistent amber banner: "SHADOW MODE — mutations are logged but not applied to Shopify"

**Typical onboarding flow:**
1. Deploy with `AGENT_MODE=shadow`
2. Connect to live Shopify store webhooks
3. Client watches dashboard + Slack for 24–48 hours
4. Operator reviews audit log for false positives
5. Flip to `AGENT_MODE=live` — no code change, no redeploy required

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Separate dev store | Doesn't use real order data; can't reproduce client's edge cases |
| Feature flag per tool | Granular but complex; shadow should be an all-or-nothing mode |
| Dry-run at Shopify API level | Shopify GraphQL does not have a dry-run mode |
| Manual review of every event pre-launch | Not scalable; misses burst patterns visible only in production traffic |

## Consequences

- **Positive:** Clients can gain confidence in the agent without any risk to live data
- **Positive:** Mode switch requires only an env var change — no redeploy needed
- **Positive:** Audit records are generated in shadow mode, giving a complete pre-launch paper trail
- **Positive:** Dashboard banner prevents operators from forgetting the agent is in shadow mode
- **Negative:** Slack notifications fire in shadow mode — clients must be briefed to not act on them until live mode is enabled (or shadow Slack channel can be configured)
- **Negative:** `verify_action` node runs but has nothing meaningful to verify in shadow mode (returns `verification_passed: True` trivially since no mutation was applied)
