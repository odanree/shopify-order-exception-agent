"""Agent tools for the order exception pipeline.

Each tool is registered with @tool from langchain_core. Services are injected at
startup via inject_tool_dependencies(); tools reference them via module-level vars
so the function signatures stay clean for LangGraph tool-calling.
"""
import contextvars
from datetime import datetime, timezone
from typing import Any

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Injected service references (set once at app startup)
# ---------------------------------------------------------------------------
_shopify_client = None
_threpl_client = None
_db_factory = None

# Context var used to accumulate tool call logs for the current request
_tool_calls_ctx: contextvars.ContextVar[list[dict]] = contextvars.ContextVar(
    "_tool_calls_ctx", default=[]
)


def inject_tool_dependencies(shopify, threpl, db_factory):
    global _shopify_client, _threpl_client, _db_factory
    _shopify_client = shopify
    _threpl_client = threpl
    _db_factory = db_factory


def _log_call(tool_name: str, args: dict, result: Any, success: bool):
    entry = {
        "tool": tool_name,
        "args": args,
        "result": result,
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "success": success,
    }
    calls = _tool_calls_ctx.get([])
    _tool_calls_ctx.set(calls + [entry])
    return entry


# ---------------------------------------------------------------------------
# Tool arg schemas
# ---------------------------------------------------------------------------

class QueryOrderArgs(BaseModel):
    order_id: str = Field(description="Shopify order ID (numeric string or GID)")


class UpdateOrderTagsArgs(BaseModel):
    order_id: str = Field(description="Shopify order ID")
    tags_to_add: list[str] = Field(default_factory=list, description="Tags to add")
    tags_to_remove: list[str] = Field(default_factory=list, description="Tags to remove")


class Notify3PLArgs(BaseModel):
    order_id: str
    reason: str
    urgency: str = Field(default="medium", description="low | medium | high")


class HoldFulfillmentArgs(BaseModel):
    order_id: str = Field(description="Shopify order ID — used to look up FulfillmentOrder IDs")
    reason: str = Field(description="FulfillmentHoldReason enum: HIGH_RISK_OF_FRAUD | INCORRECT_ADDRESS | AWAITING_PAYMENT | UNFULFILLABLE | UNKNOWN")
    note: str = Field(default="", description="Human-readable hold note surfaced to merchant")


class WriteAuditLogArgs(BaseModel):
    order_id: str
    action_taken: str
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@tool(args_schema=QueryOrderArgs)
async def query_order(order_id: str) -> dict:
    """Fetch full order details from the Shopify Admin GraphQL API."""
    args = {"order_id": order_id}
    try:
        if _shopify_client is None:
            raise RuntimeError("Shopify client not injected")
        data = await _shopify_client.get_order(order_id)
        result = {"success": True, "data": data}
        _log_call("query_order", args, result, True)
        return result
    except Exception as exc:
        logger.error("tool_query_order_failed", order_id=order_id, error=str(exc))
        result = {"success": False, "error": str(exc)}
        _log_call("query_order", args, result, False)
        return result


@tool(args_schema=UpdateOrderTagsArgs)
async def update_order_tags(
    order_id: str, tags_to_add: list[str], tags_to_remove: list[str]
) -> dict:
    """Add or remove tags on a Shopify order."""
    args = {"order_id": order_id, "tags_to_add": tags_to_add, "tags_to_remove": tags_to_remove}
    try:
        if _shopify_client is None:
            raise RuntimeError("Shopify client not injected")
        data = await _shopify_client.update_order_tags(order_id, tags_to_add, tags_to_remove)
        result = {"success": True, "data": data}
        _log_call("update_order_tags", args, result, True)
        return result
    except Exception as exc:
        logger.error("tool_update_tags_failed", order_id=order_id, error=str(exc))
        result = {"success": False, "error": str(exc)}
        _log_call("update_order_tags", args, result, False)
        return result


@tool(args_schema=Notify3PLArgs)
async def notify_3pl(order_id: str, reason: str, urgency: str = "medium") -> dict:
    """Notify the 3PL warehouse of an order exception requiring attention."""
    args = {"order_id": order_id, "reason": reason, "urgency": urgency}
    try:
        if _threpl_client is None:
            raise RuntimeError("3PL client not injected")
        success = await _threpl_client.notify_order_exception(order_id, reason, urgency)
        result = {"success": success}
        _log_call("notify_3pl", args, result, success)
        return result
    except Exception as exc:
        logger.error("tool_notify_3pl_failed", order_id=order_id, error=str(exc))
        result = {"success": False, "error": str(exc)}
        _log_call("notify_3pl", args, result, False)
        return result


@tool(args_schema=HoldFulfillmentArgs)
async def hold_fulfillment_order(order_id: str, reason: str, note: str = "") -> dict:
    """Place a FulfillmentOrder hold on a Shopify order to physically stop warehouse fulfillment."""
    args = {"order_id": order_id, "reason": reason, "note": note}
    try:
        if _shopify_client is None:
            raise RuntimeError("Shopify client not injected")
        # Get fulfillment orders for this order
        fulfillment_orders = await _shopify_client.get_fulfillment_orders(order_id)
        if not fulfillment_orders:
            result = {"success": False, "error": "No open fulfillment orders found"}
            _log_call("hold_fulfillment_order", args, result, False)
            return result

        held = []
        for fo in fulfillment_orders:
            fo_id = fo.get("id", "")
            fo_status = fo.get("status", "")
            # Only hold open/in-progress fulfillment orders
            if fo_status in ("OPEN", "IN_PROGRESS"):
                held_fo = await _shopify_client.hold_fulfillment_order(fo_id, reason, note)
                held.append(held_fo)

        result = {"success": True, "held_count": len(held), "fulfillment_orders": held}
        _log_call("hold_fulfillment_order", args, result, True)
        return result
    except Exception as exc:
        logger.error("tool_hold_fulfillment_failed", order_id=order_id, error=str(exc))
        result = {"success": False, "error": str(exc)}
        _log_call("hold_fulfillment_order", args, result, False)
        return result


@tool(args_schema=WriteAuditLogArgs)
async def write_audit_log(order_id: str, action_taken: str, metadata: dict) -> dict:
    """Write a structured audit log entry to PostgreSQL."""
    args = {"order_id": order_id, "action_taken": action_taken, "metadata": metadata}
    try:
        if _db_factory is None:
            raise RuntimeError("DB factory not injected")
        from app.models.db import AuditLog

        async with _db_factory() as session:
            input_tokens = metadata.get("input_tokens")
            output_tokens = metadata.get("output_tokens")
            # claude-sonnet-4-6: $3/MTok input, $15/MTok output
            cost_usd = None
            if input_tokens is not None and output_tokens is not None:
                cost_usd = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000

            record = AuditLog(
                webhook_id=metadata.get("webhook_id", "unknown"),
                order_id=order_id,
                event_type=metadata.get("event_type", "unknown"),
                exception_type=metadata.get("exception_type"),
                action_taken=action_taken,
                tool_calls=metadata.get("tool_calls_log"),
                metadata_=metadata,
                processing_time_ms=metadata.get("processing_time_ms"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

        result = {"success": True, "audit_id": str(record.id)}
        _log_call("write_audit_log", args, result, True)
        return result
    except Exception as exc:
        logger.error("tool_write_audit_log_failed", order_id=order_id, error=str(exc))
        result = {"success": False, "error": str(exc)}
        _log_call("write_audit_log", args, result, False)
        return result


ALL_TOOLS = [query_order, update_order_tags, hold_fulfillment_order, notify_3pl, write_audit_log]
