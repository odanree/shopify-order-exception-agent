# ADR 007 — FulfillmentOrder Hold via Shopify GraphQL API

**Status:** Accepted  
**Date:** 2026-04-03

## Context

The initial implementation tagged orders with exception labels (e.g. `exception:fraud-risk`) but did not physically prevent fulfillment. A warehouse fulfillment system polling Shopify could still pick up and ship a fraud-risk or address-invalid order before an operator reviewed the tag. Tagging alone is informational; it does not integrate with Shopify's fulfillment flow.

Shopify exposes a `fulfillmentOrderHold` mutation that places a hold on a `FulfillmentOrder` object, surfacing a hold reason to the merchant dashboard and blocking the warehouse assignment from progressing.

## Decision

Add a `hold_fulfillment_order` LangGraph tool that calls `fulfillmentOrderHold` after tagging, for exception types where a physical stop is appropriate.

**Hold reason mapping** (Shopify `FulfillmentHoldReason` enum):

| Exception type | Hold reason |
|----------------|-------------|
| `fraud_risk` | `HIGH_RISK_OF_FRAUD` |
| `address_invalid` | `INCORRECT_ADDRESS` |
| `payment_issue` | `AWAITING_PAYMENT` |
| `fulfillment_delay` | `UNFULFILLABLE` |
| `high_value` | *(no hold — tagging sufficient)* |

**Failure mode:** Hold failure is **non-fatal**. If `fulfillmentOrderHold` returns errors (e.g. FulfillmentOrder is already shipped, no open orders), the agent logs a warning and proceeds. Tagging is always the minimum viable action.

The `fulfillment_held` boolean is recorded in state and persisted in the audit log for operator visibility.

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Tag-only (status quo) | Informational only; warehouse can bypass tags |
| Order-level cancel | Too destructive; many fraud flags are false positives requiring review, not cancellation |
| Webhook to 3PL | 3PL integration is already handled by the `notify_3pl` path; hold is a Shopify-native signal |
| Always hold all exception types | `high_value` orders do not require a fulfillment stop — over-holding increases operator workload |

## Consequences

- **Positive:** Fraud-risk and address-invalid orders are physically blocked at the Shopify layer, not just tagged
- **Positive:** Merchants see a structured hold reason in the Shopify admin UI
- **Positive:** Hold failure is non-fatal so a Shopify API hiccup does not prevent tagging or audit
- **Negative:** Adds a second Shopify GraphQL call per applicable event (rate-limit budget cost ~100 points)
- **Negative:** FulfillmentOrders can only be held if `status` is `OPEN` or `IN_PROGRESS`; already-shipped orders silently skip the hold
