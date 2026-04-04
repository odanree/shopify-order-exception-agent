import asyncio
import random
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# GraphQL query / mutation strings
# ---------------------------------------------------------------------------

ORDER_QUERY = """
query GetOrder($id: ID!) {
  order(id: $id) {
    id
    name
    email
    tags
    displayFinancialStatus
    displayFulfillmentStatus
    totalPriceSet { shopMoney { amount currencyCode } }
    lineItems(first: 50) {
      edges {
        node {
          id
          sku
          title
          quantity
          originalUnitPriceSet { shopMoney { amount } }
          variant { id }
        }
      }
    }
    customer { id email firstName lastName }
    shippingAddress {
      firstName lastName address1 address2
      city province zip country phone
    }
    riskLevel
    createdAt
  }
}
"""

TAGS_ADD_MUTATION = """
mutation TagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

TAGS_REMOVE_MUTATION = """
mutation TagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

FULFILLMENT_ORDERS_QUERY = """
query GetFulfillmentOrders($orderId: ID!) {
  order(id: $orderId) {
    fulfillmentOrders(first: 5) {
      edges {
        node {
          id
          status
          requestStatus
          assignedLocation { id name }
        }
      }
    }
  }
}
"""

FULFILLMENT_HOLD_MUTATION = """
mutation FulfillmentOrderHold($id: ID!, $reason: FulfillmentHoldReason!, $note: String) {
  fulfillmentOrderHold(id: $id, holdInput: { reason: $reason, notifyMerchant: false, holdNote: $note }) {
    fulfillmentOrder { id status }
    userErrors { field message }
  }
}
"""


class ShopifyGraphQLClient:
    BUCKET_KEY = "shopify:bucket:available"
    ESTIMATED_QUERY_COST = 100  # conservative default before we know actual cost
    MAX_BUCKET = 1000.0
    RESTORE_RATE = 50.0  # points/second

    def __init__(self, domain: str, token: str, redis_client, restore_rate: float = 50.0):
        self.endpoint = f"https://{domain}/admin/api/2024-01/graphql.json"
        self.headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        }
        self.redis = redis_client
        self.restore_rate = restore_rate
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _wait_for_budget(self):
        """Block until the rate-limit bucket has enough headroom."""
        raw = await self.redis.get(self.BUCKET_KEY)
        available = float(raw) if raw is not None else self.MAX_BUCKET

        if available < self.ESTIMATED_QUERY_COST:
            wait_secs = (self.ESTIMATED_QUERY_COST - available) / self.restore_rate
            logger.info(
                "shopify_bucket_throttle_wait",
                available=available,
                wait_secs=round(wait_secs, 2),
            )
            await asyncio.sleep(wait_secs)

    async def _update_bucket(self, throttle_status: dict):
        """Persist the currentlyAvailable value Shopify returned."""
        currently = throttle_status.get("currentlyAvailable")
        if currently is not None:
            await self.redis.set(self.BUCKET_KEY, str(float(currently)))

    async def execute(
        self, query: str, variables: dict | None = None, max_retries: int = 3
    ) -> dict[str, Any]:
        """Execute a GraphQL query/mutation with cost-budget awareness and retry on throttle."""
        await self._wait_for_budget()

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = await self._client.post(
                    self.endpoint,
                    headers=self.headers,
                    json={"query": query, "variables": variables or {}},
                )
                resp.raise_for_status()
                body = resp.json()

                # Update bucket from response extension
                extensions = body.get("extensions", {})
                cost_info = extensions.get("cost", {})
                throttle_status = cost_info.get("throttleStatus", {})
                if throttle_status:
                    await self._update_bucket(throttle_status)

                # Handle GraphQL-level errors
                errors = body.get("errors", [])
                if errors:
                    for err in errors:
                        if err.get("extensions", {}).get("code") == "THROTTLED":
                            # Exponential backoff with jitter
                            backoff = (2 ** attempt) + random.uniform(-0.5, 0.5)
                            logger.warning(
                                "shopify_graphql_throttled",
                                attempt=attempt + 1,
                                backoff_secs=round(backoff, 2),
                            )
                            await asyncio.sleep(max(backoff, 0.1))
                            last_error = RuntimeError("Shopify API throttled")
                            break
                    else:
                        # Non-throttle errors — raise immediately
                        raise RuntimeError(f"GraphQL errors: {errors}")
                    continue  # retry on throttle

                return body.get("data", {})

            except httpx.HTTPStatusError as exc:
                logger.error("shopify_http_error", status=exc.response.status_code, attempt=attempt)
                last_error = exc
                if exc.response.status_code < 500:
                    raise
                backoff = (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(backoff)

        raise last_error or RuntimeError("GraphQL request failed after retries")

    async def get_order(self, order_id: str) -> dict:
        """Fetch full order details. order_id can be numeric or GID."""
        gid = order_id if order_id.startswith("gid://") else f"gid://shopify/Order/{order_id}"
        data = await self.execute(ORDER_QUERY, {"id": gid})
        return data.get("order", {})

    async def update_order_tags(
        self, order_id: str, tags_to_add: list[str], tags_to_remove: list[str]
    ) -> dict:
        gid = order_id if order_id.startswith("gid://") else f"gid://shopify/Order/{order_id}"
        result = {}
        if tags_to_add:
            data = await self.execute(TAGS_ADD_MUTATION, {"id": gid, "tags": tags_to_add})
            result["add"] = data.get("tagsAdd", {})
        if tags_to_remove:
            data = await self.execute(TAGS_REMOVE_MUTATION, {"id": gid, "tags": tags_to_remove})
            result["remove"] = data.get("tagsRemove", {})
        return result

    async def get_fulfillment_orders(self, order_id: str) -> list[dict]:
        """Return open FulfillmentOrder nodes for an order."""
        gid = order_id if order_id.startswith("gid://") else f"gid://shopify/Order/{order_id}"
        data = await self.execute(FULFILLMENT_ORDERS_QUERY, {"orderId": gid})
        edges = (data.get("order") or {}).get("fulfillmentOrders", {}).get("edges", [])
        return [e["node"] for e in edges]

    async def hold_fulfillment_order(
        self, fulfillment_order_id: str, reason: str, note: str = ""
    ) -> dict:
        """Place a hold on a FulfillmentOrder. reason must be a FulfillmentHoldReason enum value."""
        data = await self.execute(
            FULFILLMENT_HOLD_MUTATION,
            {"id": fulfillment_order_id, "reason": reason, "note": note or None},
        )
        result = data.get("fulfillmentOrderHold", {})
        errors = result.get("userErrors", [])
        if errors:
            raise RuntimeError(f"FulfillmentOrder hold errors: {errors}")
        return result.get("fulfillmentOrder", {})

    async def close(self):
        await self._client.aclose()
