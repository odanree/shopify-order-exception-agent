"""Background DLQ retry worker.

Run as: python -m app.worker

Scans the dead_letter_events table every 5 minutes for events that:
- Are not yet resolved
- Have retry_count < 5
- Last attempt was more than 15 minutes ago (or never attempted)

Re-runs the agent graph for each qualifying event.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

logger = structlog.get_logger()

POLL_INTERVAL_SECONDS = 300  # 5 minutes
MAX_RETRIES = 5
RETRY_COOLDOWN_MINUTES = 15


async def process_pending_dlq_events():
    from app.db.session import AsyncSessionLocal, init_db
    from app.models.db import DeadLetterEvent
    from app.agent.graph import process_webhook_event
    from app.agent.state import OrderExceptionState

    await init_db()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RETRY_COOLDOWN_MINUTES)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(DeadLetterEvent)
            .where(DeadLetterEvent.resolved_at.is_(None))
            .where(DeadLetterEvent.retry_count < MAX_RETRIES)
            .where(
                (DeadLetterEvent.last_attempt_at.is_(None))
                | (DeadLetterEvent.last_attempt_at < cutoff)
            )
        )
        result = await session.execute(stmt)
        events = result.scalars().all()

    logger.info("dlq_worker_scan", events_found=len(events))

    for event in events:
        state: OrderExceptionState = {
            "webhook_id": f"dlq-retry-{event.webhook_id}",
            "event_type": event.event_type,
            "order_id": event.order_id,
            "raw_payload": event.raw_payload or {},
            "exception_type": None,
            "routing_decision": None,
            "tool_calls_log": [],
            "error": None,
            "retry_count": 0,
            "processing_start_ms": int(time.time() * 1000),
            "messages": [],
        }
        try:
            await process_webhook_event(state)
            async with AsyncSessionLocal() as session:
                db_event = await session.get(DeadLetterEvent, event.id)
                if db_event:
                    db_event.resolved_at = datetime.now(timezone.utc)
                    db_event.retry_count += 1
                    db_event.last_attempt_at = datetime.now(timezone.utc)
                    await session.commit()
            logger.info("dlq_retry_success", order_id=event.order_id)
        except Exception as exc:
            async with AsyncSessionLocal() as session:
                db_event = await session.get(DeadLetterEvent, event.id)
                if db_event:
                    db_event.retry_count += 1
                    db_event.last_attempt_at = datetime.now(timezone.utc)
                    db_event.error_message = str(exc)
                    await session.commit()
            logger.error("dlq_retry_failed", order_id=event.order_id, error=str(exc))


async def main():
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("dlq_worker_started", poll_interval=POLL_INTERVAL_SECONDS)

    # Wire up service dependencies
    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.shopify_client import ShopifyGraphQLClient
    from app.services.slack_client import SlackClient
    from app.services.threpl_client import ThreePLClient
    from app.agent.tools import inject_tool_dependencies
    from app.db.session import AsyncSessionLocal

    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    shopify = ShopifyGraphQLClient(
        domain=settings.active_shopify_domain,
        token=settings.active_shopify_token,
        redis_client=redis_client,
    )
    slack = SlackClient(settings.slack_webhook_url)
    threpl = ThreePLClient(settings.threpl_webhook_url, settings.threpl_api_key)
    inject_tool_dependencies(shopify=shopify, slack=slack, threpl=threpl, db_factory=AsyncSessionLocal)

    while True:
        try:
            await process_pending_dlq_events()
        except Exception as exc:
            logger.error("dlq_worker_cycle_error", error=str(exc))
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
