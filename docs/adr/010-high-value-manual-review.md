# ADR 010 — High-Value Order Manual Review Threshold

**Status:** Accepted  
**Date:** 2026-04-03

## Context

SMB clients are often most anxious about the agent taking automated action on high-value orders. A $1,200 order that gets a fulfillment hold applied incorrectly is a meaningful business incident. The LLM classifier is accurate for typical orders, but the operator needs a safety net for high-ticket items regardless of exception type classification.

The existing `high_value` exception type handles orders >$1,000 by tagging and Slack-alerting — but this requires the LLM to correctly classify the order as high_value first. The threshold check should be a hard rule in the decision layer, not dependent on LLM output.

## Decision

Add `HIGH_VALUE_REVIEW_THRESHOLD_USD=500.0` to config (default $500). In `make_decision`, after the LLM classification is resolved, check `raw_payload.total_price` against the threshold. If exceeded:

- `routing_decision` is overridden to `"require_manual_review"` regardless of exception type
- In `execute_action`, the `require_manual_review` branch:
  1. Tags the order with `exception:manual-review-required`
  2. Emits a **critical** Slack alert: "Manual Review Required — Order `{id}` total $X exceeds $500 threshold"
  3. Does **not** apply a fulfillment hold — operator decides after reviewing
  4. Does **not** notify 3PL
- The order remains in Shopify awaiting operator action; the agent does not block fulfillment

**Rationale for $500 default:** Low enough to catch most "feels high-risk" orders for a small business; configurable so each client can set their own comfort threshold.

**Rationale for no automated hold:** A high-value order is not inherently problematic — it just warrants human eyes. Automatically holding it would disrupt fulfillment for legitimate premium purchases.

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Rely on LLM high_value classification | LLM may classify a $600 fraud order as fraud_risk, bypassing this path |
| Always hold orders above threshold | Over-disruptive; most high-value orders are legitimate |
| Separate threshold per exception type | Adds config complexity; the blanket rule is simpler to explain to SMB clients |
| Use Shopify order risk level instead | Risk level doesn't correlate with order value |

## Consequences

- **Positive:** Clients can set a specific dollar amount above which the agent never acts autonomously — easy to communicate and sell as a safety feature
- **Positive:** The check is in the decision layer (rule-based), not dependent on LLM accuracy
- **Positive:** Threshold is configurable via env var — no code change per client
- **Negative:** All high-value orders generate a Slack alert even if genuinely routine — potential alert fatigue if threshold is set too low
- **Negative:** The agent does not resume processing after a manual review (no HitL interrupt on this path) — the operator must take action in Shopify directly. A full HitL approval flow could be added in a future iteration.
