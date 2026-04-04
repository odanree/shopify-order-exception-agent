"""LangGraph node implementations for the order exception state machine."""
import time
import traceback
from datetime import datetime, timezone

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import OrderExceptionState
from app.config import get_settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Event router (set by inject_event_router() in main.py)
# ---------------------------------------------------------------------------
_event_router = None


def inject_event_router(router) -> None:
    global _event_router
    _event_router = router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXCEPTION_TRIAGE_SYSTEM = """You are an order exception classifier for an e-commerce operations system.
Given a Shopify order payload, classify the primary exception type into exactly one of these categories:
- fraud_risk: order has high fraud indicators (unusual address, risk level, velocity)
- address_invalid: shipping address appears incomplete, invalid, or undeliverable
- high_value: order total exceeds $1000 USD
- fulfillment_delay: fulfillment or shipping is delayed beyond expected window
- payment_issue: payment failed, chargeback risk, or authorization declined
- unknown: does not fit other categories

Respond with ONLY the category name, nothing else."""

# Maps exception types to Shopify FulfillmentHoldReason enum values.
# Exception types not present here do NOT trigger a fulfillment hold.
FULFILLMENT_HOLD_REASONS: dict[str, str] = {
    "fraud_risk": "HIGH_RISK_OF_FRAUD",
    "address_invalid": "INCORRECT_ADDRESS",
    "payment_issue": "AWAITING_PAYMENT",
    "fulfillment_delay": "UNFULFILLABLE",
}

ROUTING_MAP = {
    "fraud_risk": "tag_slack_and_3pl",
    "address_invalid": "tag_and_slack",
    "high_value": "tag_and_slack",
    "fulfillment_delay": "tag_and_3pl",
    "payment_issue": "escalate",
    "unknown": "tag_only",
    "require_manual_review": "require_manual_review",
}

EXCEPTION_TAGS = {
    "fraud_risk": "exception:fraud-risk",
    "address_invalid": "exception:address-invalid",
    "high_value": "exception:high-value",
    "fulfillment_delay": "exception:fulfillment-delay",
    "payment_issue": "exception:payment-issue",
    "unknown": "exception:unknown",
    "require_manual_review": "exception:manual-review-required",
}


def _get_llm():
    settings = get_settings()
    return ChatAnthropic(
        model=settings.agent_model,
        api_key=settings.anthropic_api_key,
        max_tokens=256,
    )


def _get_langfuse_handler(session_id: str, node_name: str, tags: list[str] | None = None):
    """Return a LangFuse CallbackHandler for the given session, or None if disabled/unavailable."""
    try:
        settings = get_settings()
        if not settings.langfuse_enabled or not settings.langfuse_public_key:
            return None
        from langfuse.callback import CallbackHandler
        return CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            trace_name=node_name,
            session_id=session_id,
            user_id="system",
            tags=tags or ["order-exception-agent"],
        )
    except ImportError:
        return None
    except Exception as exc:
        logger.warning("langfuse_handler_init_failed", node=node_name, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def triage_event(state: OrderExceptionState) -> OrderExceptionState:
    """Classify the order event into an exception type using Claude."""
    order_id = state["order_id"]
    payload = state["raw_payload"]

    # Build a compact order summary for the LLM
    total = payload.get("total_price", "0.00")
    risk = payload.get("risk_level", "unknown")
    financial = payload.get("financial_status", "unknown")
    fulfillment = payload.get("fulfillment_status", "unknown")
    address = payload.get("shipping_address", {})
    event_type = state["event_type"]

    prompt = (
        f"Event type: {event_type}\n"
        f"Order total: ${total}\n"
        f"Financial status: {financial}\n"
        f"Fulfillment status: {fulfillment}\n"
        f"Risk level: {risk}\n"
        f"Shipping address present: {bool(address)}\n"
        f"Address country: {address.get('country', 'unknown')}\n"
        f"Tags: {payload.get('tags', '')}\n"
    )

    try:
        llm = _get_llm()
        messages = [
            SystemMessage(content=EXCEPTION_TRIAGE_SYSTEM),
            HumanMessage(content=prompt),
        ]
        lf_handler = _get_langfuse_handler(state["webhook_id"], "triage_event")
        invoke_config = {"callbacks": [lf_handler]} if lf_handler else {}
        response = await llm.ainvoke(messages, config=invoke_config)
        exception_type = response.content.strip().lower()
        if exception_type not in ROUTING_MAP:
            exception_type = "unknown"
        if lf_handler:
            lf_handler.flush()
    except Exception as exc:
        logger.error("triage_llm_failed", order_id=order_id, error=str(exc))
        exception_type = "unknown"

    logger.info("triage_complete", order_id=order_id, exception_type=exception_type)
    return {
        **state,
        "exception_type": exception_type,
        "messages": state.get("messages", []),
    }


async def make_decision(state: OrderExceptionState) -> OrderExceptionState:
    """Rule-based routing: map exception type → action decision.

    High-value orders (total > high_value_review_threshold_usd) override automated
    routing to require_manual_review regardless of exception type.
    """
    settings = get_settings()
    exception_type = state.get("exception_type") or "unknown"
    routing = ROUTING_MAP.get(exception_type, "tag_only")

    # High-value override: any order above the threshold bypasses automated action
    try:
        order_total = float(state["raw_payload"].get("total_price", "0.00") or "0.00")
    except (ValueError, TypeError):
        order_total = 0.0

    if order_total > settings.high_value_review_threshold_usd:
        routing = "require_manual_review"
        logger.info(
            "high_value_override",
            order_id=state["order_id"],
            order_total=order_total,
            threshold=settings.high_value_review_threshold_usd,
        )

    logger.info("decision_made", order_id=state["order_id"], routing=routing)
    return {**state, "routing_decision": routing}


async def execute_action(state: OrderExceptionState) -> OrderExceptionState:
    """Execute the tool calls dictated by routing_decision."""
    from app.agent.tools import (
        hold_fulfillment_order,
        notify_3pl,
        update_order_tags,
        _tool_calls_ctx,
    )
    from app.config import get_settings

    order_id = state["order_id"]
    routing = state.get("routing_decision", "tag_only")
    exception_type = state.get("exception_type", "unknown")
    exception_tag = EXCEPTION_TAGS.get(
        routing if routing == "require_manual_review" else exception_type,
        "exception:unknown",
    )
    settings = get_settings()

    # Shadow mode: log the full intended action but skip all write mutations
    if settings.agent_mode == "shadow":
        logger.info(
            "execute_action_shadowed",
            order_id=order_id,
            routing=routing,
            exception_type=exception_type,
            would_apply_tag=exception_tag,
            would_hold=routing in FULFILLMENT_HOLD_REASONS,
        )
        return {
            **state,
            "shadowed": True,
            "tool_calls_log": state.get("tool_calls_log", []),
            "fulfillment_held": False,
            "error": None,
            "retry_count": state.get("retry_count", 0),
        }

    # Reset context var for this invocation and start with existing log
    _tool_calls_ctx.set(list(state.get("tool_calls_log", [])))

    error = None
    fulfillment_held = False

    try:
        # Always tag the order with the exception type
        tag_result = await update_order_tags.ainvoke(
            {"order_id": order_id, "tags_to_add": [exception_tag], "tags_to_remove": []}
        )
        if not tag_result.get("success"):
            raise RuntimeError(f"update_order_tags failed: {tag_result.get('error')}")

        # FulfillmentOrder hold — physically stops the warehouse from shipping
        hold_reason = FULFILLMENT_HOLD_REASONS.get(exception_type)
        if hold_reason:
            hold_note = (
                f"Held by exception agent: {exception_type.replace('_', ' ')} — "
                f"routing={routing}"
            )
            hold_result = await hold_fulfillment_order.ainvoke(
                {"order_id": order_id, "reason": hold_reason, "note": hold_note}
            )
            fulfillment_held = bool(hold_result.get("success") and hold_result.get("held_count", 0) > 0)
            if not hold_result.get("success"):
                # Non-fatal: log warning but continue — tag is the minimum viable action
                logger.warning(
                    "fulfillment_hold_failed",
                    order_id=order_id,
                    reason=hold_reason,
                    error=hold_result.get("error"),
                )

        # High-value manual review — tag + critical Slack alert, no automated hold
        if routing == "require_manual_review":
            order_total = state["raw_payload"].get("total_price", "0.00")
            msg = (
                f"🔴 *Manual Review Required* — Order `{order_id}`\n"
                f"Order total: *${order_total}* exceeds the ${settings.high_value_review_threshold_usd:.0f} threshold.\n"
                f"Exception type: `{exception_type}` | Please review in Shopify before any action."
            )
            if _event_router is not None:
                await _event_router.emit(
                    "order_exception_alert",
                    {
                        "channel": settings.slack_default_channel,
                        "order_id": order_id,
                        "message": msg,
                        "severity": "critical",
                    },
                )
            updated_log = _tool_calls_ctx.get([])
            return {
                **state,
                "tool_calls_log": updated_log,
                "fulfillment_held": False,
                "error": None,
                "retry_count": state.get("retry_count", 0),
                "shadowed": False,
            }

        # Slack notification — emitted to pub/sub router (clawhip pattern)
        if routing in ("tag_and_slack", "tag_slack_and_3pl", "escalate"):
            severity = "critical" if routing == "escalate" else "warning"
            msg = (
                f"Order exception detected: *{exception_type.replace('_', ' ').title()}*\n"
                f"Routing: `{routing}` | Order: `{order_id}`"
                + (f" | Fulfillment hold applied" if fulfillment_held else "")
            )
            if _event_router is not None:
                await _event_router.emit(
                    "order_exception_alert",
                    {
                        "channel": settings.slack_default_channel,
                        "order_id": order_id,
                        "message": msg,
                        "severity": severity,
                    },
                )

        # 3PL notification
        if routing in ("tag_and_3pl", "tag_slack_and_3pl"):
            urgency = "high" if exception_type == "fraud_risk" else "medium"
            await notify_3pl.ainvoke(
                {
                    "order_id": order_id,
                    "reason": exception_type.replace("_", " "),
                    "urgency": urgency,
                }
            )

    except Exception as exc:
        error = str(exc)
        logger.error("execute_action_failed", order_id=order_id, error=error)

    updated_log = _tool_calls_ctx.get([])
    retry_count = state.get("retry_count", 0)
    if error:
        retry_count += 1

    return {
        **state,
        "tool_calls_log": updated_log,
        "fulfillment_held": fulfillment_held,
        "shadowed": False,
        "error": error,
        "retry_count": retry_count,
    }


async def record_audit(state: OrderExceptionState) -> OrderExceptionState:
    """Write the final audit record to PostgreSQL."""
    from app.agent.tools import write_audit_log

    order_id = state["order_id"]
    processing_time_ms = int(time.time() * 1000) - state.get("processing_start_ms", 0)

    await write_audit_log.ainvoke(
        {
            "order_id": order_id,
            "action_taken": state.get("routing_decision", "unknown"),
            "metadata": {
                "webhook_id": state["webhook_id"],
                "event_type": state["event_type"],
                "exception_type": state.get("exception_type"),
                "tool_calls_log": state.get("tool_calls_log", []),
                "processing_time_ms": processing_time_ms,
                "error": state.get("error"),
            },
        }
    )

    logger.info(
        "audit_complete",
        order_id=order_id,
        exception_type=state.get("exception_type"),
        processing_time_ms=processing_time_ms,
    )
    return state


async def verify_action(state: OrderExceptionState) -> OrderExceptionState:
    """Re-query Shopify to confirm the exception tag was actually applied.

    If execute_action itself raised an error, skip verification — the error routing
    will handle retry/dead-letter. Otherwise, verify the tag is present and retry
    execute_action up to 2 times before dead-lettering.
    """
    from app.agent.tools import _shopify_client

    order_id = state["order_id"]
    exception_type = state.get("exception_type", "unknown")
    expected_tag = EXCEPTION_TAGS.get(exception_type, "exception:unknown")

    # If execute_action failed outright, skip verification and let routing handle it
    if state.get("error"):
        return {**state, "verification_passed": False}

    verification_passed = False
    error = None

    try:
        if _shopify_client is None:
            raise RuntimeError("Shopify client not injected")

        order_data = await _shopify_client.get_order(order_id)
        # Shopify GraphQL returns tags as a list of strings
        current_tags = order_data.get("tags") or []
        if isinstance(current_tags, str):
            current_tags = [t.strip() for t in current_tags.split(",") if t.strip()]

        verification_passed = expected_tag in current_tags

        if not verification_passed:
            logger.warning(
                "verify_action_tag_missing",
                order_id=order_id,
                expected_tag=expected_tag,
                current_tags=current_tags,
                retry_count=state.get("retry_count", 0),
            )
    except Exception as exc:
        error = str(exc)
        logger.error("verify_action_query_failed", order_id=order_id, error=error)

    retry_count = state.get("retry_count", 0)
    if not verification_passed:
        retry_count += 1
        error = error or f"verification_failed: tag '{expected_tag}' not present in Shopify"

    logger.info(
        "verify_action_complete",
        order_id=order_id,
        verification_passed=verification_passed,
        retry_count=retry_count,
    )
    return {
        **state,
        "verification_passed": verification_passed,
        "retry_count": retry_count,
        "error": error if not verification_passed else state.get("error"),
    }


async def handle_dead_letter(state: OrderExceptionState) -> OrderExceptionState:
    """Persist failed events to the dead-letter table for operator review."""
    from app.db.session import AsyncSessionLocal
    from app.models.db import DeadLetterEvent

    order_id = state["order_id"]
    logger.error(
        "dead_letter_event",
        order_id=order_id,
        webhook_id=state["webhook_id"],
        error=state.get("error"),
        retry_count=state.get("retry_count"),
    )

    try:
        import sentry_sdk
        sentry_sdk.capture_message(
            f"Order exception dead-letter: {order_id}",
            level="error",
            extras={
                "order_id": order_id,
                "webhook_id": state["webhook_id"],
                "error": state.get("error"),
                "retry_count": state.get("retry_count"),
                "exception_type": state.get("exception_type"),
            },
        )
    except ImportError:
        pass

    try:
        if AsyncSessionLocal is not None:
            async with AsyncSessionLocal() as session:
                record = DeadLetterEvent(
                    webhook_id=state["webhook_id"],
                    order_id=order_id,
                    event_type=state["event_type"],
                    raw_payload=state.get("raw_payload"),
                    error_message=state.get("error"),
                    error_traceback=traceback.format_exc(),
                    retry_count=state.get("retry_count", 0),
                    last_attempt_at=datetime.now(timezone.utc),
                )
                session.add(record)
                await session.commit()
    except Exception as exc:
        logger.error("dead_letter_persist_failed", order_id=order_id, error=str(exc))

    return state
