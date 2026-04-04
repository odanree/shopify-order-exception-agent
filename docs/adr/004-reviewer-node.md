# ADR 004 — Reviewer Node for Action Verification

**Status:** Accepted  
**Date:** 2026-04-03

## Context

After `execute_action` runs (tagging orders, notifying 3PL), there was no confirmation that the Shopify state actually reflected the change. Network faults, transient Shopify errors, or rate-limit throttling can cause a mutation to return 200 but not persist. The agent would proceed to audit without knowing whether the action succeeded.

The claw-code agent architecture describes a "reviewer" as a critical missing piece in most production agent pipelines: a node that re-queries the system of record to close the verification loop before recording success.

## Decision

Add a `verify_action` node between `execute_action` and `audit`. The node:

1. Re-queries the Shopify order for its current tags via `get_order`.
2. Checks whether the expected exception tag (e.g., `exception:fraud-risk`) is present.
3. If the tag is absent and retry attempts remain (`retry_count < 2`), routes back to `execute_action` to retry.
4. If retries are exhausted, routes to `dead_letter` rather than silently auditing a failed action.
5. If `execute_action` itself raised an error (state has `error` set), skips tag verification — the error routing handles that path.

The routing function `_route_after_verify` replaces the previous `_route_after_action`.

## Consequences

**Positive:**
- Agent state reflects ground truth (Shopify) rather than optimistic mutation results.
- Persistent Shopify failures are dead-lettered for operator review instead of silently passing.
- The retry cycle is bounded (max 2 retries) to prevent infinite loops.

**Negative:**
- Each workflow now makes one additional Shopify API read (the verification query), consuming rate-limit budget.
- Adds one node to the graph, slightly increasing execution time per event (~200–500ms typical).
- For `tag_only` routing (no Slack/3PL), verification is still performed — this could be optimized to skip if the only action was tagging and it succeeded without error.
