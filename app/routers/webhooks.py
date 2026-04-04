"""Shopify webhook endpoints.

Shopify requires a 200 response within 5 seconds. We return immediately and
hand off all processing to a FastAPI BackgroundTask.
"""
import time

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.agent.graph import process_webhook_event
from app.agent.state import OrderExceptionState
from app.middleware.verify_webhook import verify_shopify_webhook
from app.services.idempotency import IdempotencyService

logger = structlog.get_logger()

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _get_idempotency(request: Request) -> IdempotencyService:
    return request.app.state.idempotency


async def _run_agent(state: OrderExceptionState) -> None:
    try:
        await process_webhook_event(state)
    except Exception as exc:
        logger.error(
            "agent_background_task_failed",
            webhook_id=state["webhook_id"],
            order_id=state["order_id"],
            error=str(exc),
            exc_info=True,
        )


def _extract_order_id(payload: dict) -> str:
    return str(payload.get("id", "unknown"))


@router.post("/orders/create")
async def order_created(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict = Depends(verify_shopify_webhook),
    idempotency: IdempotencyService = Depends(_get_idempotency),
):
    webhook_id = request.headers.get("X-Shopify-Webhook-Id", f"no-id-{time.time()}")
    order_id = _extract_order_id(payload)

    if not await idempotency.mark_processed(webhook_id):
        return {"status": "duplicate", "webhook_id": webhook_id}

    state: OrderExceptionState = {
        "webhook_id": webhook_id,
        "event_type": "orders/create",
        "order_id": order_id,
        "raw_payload": payload,
        "exception_type": None,
        "routing_decision": None,
        "tool_calls_log": [],
        "error": None,
        "retry_count": 0,
        "processing_start_ms": int(time.time() * 1000),
        "messages": [],
    }

    background_tasks.add_task(_run_agent, state)
    logger.info("webhook_accepted", event="orders/create", order_id=order_id)
    return {"status": "accepted", "webhook_id": webhook_id, "order_id": order_id}


@router.post("/orders/updated")
async def order_updated(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict = Depends(verify_shopify_webhook),
    idempotency: IdempotencyService = Depends(_get_idempotency),
):
    webhook_id = request.headers.get("X-Shopify-Webhook-Id", f"no-id-{time.time()}")
    order_id = _extract_order_id(payload)

    if not await idempotency.mark_processed(webhook_id):
        return {"status": "duplicate", "webhook_id": webhook_id}

    state: OrderExceptionState = {
        "webhook_id": webhook_id,
        "event_type": "orders/updated",
        "order_id": order_id,
        "raw_payload": payload,
        "exception_type": None,
        "routing_decision": None,
        "tool_calls_log": [],
        "error": None,
        "retry_count": 0,
        "processing_start_ms": int(time.time() * 1000),
        "messages": [],
    }

    background_tasks.add_task(_run_agent, state)
    logger.info("webhook_accepted", event="orders/updated", order_id=order_id)
    return {"status": "accepted", "webhook_id": webhook_id, "order_id": order_id}


@router.post("/fulfillments/updated")
async def fulfillment_updated(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict = Depends(verify_shopify_webhook),
    idempotency: IdempotencyService = Depends(_get_idempotency),
):
    webhook_id = request.headers.get("X-Shopify-Webhook-Id", f"no-id-{time.time()}")
    order_id = str(payload.get("order_id", "unknown"))

    if not await idempotency.mark_processed(webhook_id):
        return {"status": "duplicate", "webhook_id": webhook_id}

    state: OrderExceptionState = {
        "webhook_id": webhook_id,
        "event_type": "fulfillment_events/create",
        "order_id": order_id,
        "raw_payload": payload,
        "exception_type": None,
        "routing_decision": None,
        "tool_calls_log": [],
        "error": None,
        "retry_count": 0,
        "processing_start_ms": int(time.time() * 1000),
        "messages": [],
    }

    background_tasks.add_task(_run_agent, state)
    logger.info("webhook_accepted", event="fulfillment_events/create", order_id=order_id)
    return {"status": "accepted", "webhook_id": webhook_id, "order_id": order_id}
