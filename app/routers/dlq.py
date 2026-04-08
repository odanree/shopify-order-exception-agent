"""Dead-letter queue management endpoints."""
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.db import DeadLetterEvent

logger = structlog.get_logger()

router = APIRouter(prefix="/api/dlq", tags=["dlq"])


@router.get("")
async def list_dlq_events(
    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)
):
    """List unresolved dead-letter events, paginated."""
    stmt = (
        select(DeadLetterEvent)
        .where(DeadLetterEvent.resolved_at.is_(None))
        .order_by(DeadLetterEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    return {
        "events": [
            {
                "id": str(e.id),
                "webhook_id": e.webhook_id,
                "order_id": e.order_id,
                "event_type": e.event_type,
                "error_message": e.error_message,
                "retry_count": e.retry_count,
                "last_attempt_at": e.last_attempt_at.isoformat() if e.last_attempt_at else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post("/{event_id}/resolve")
async def resolve_dlq_event(event_id: str, db: AsyncSession = Depends(get_db)):
    """Mark a dead-letter event as resolved."""
    try:
        uid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid event_id format")

    event = await db.get(DeadLetterEvent, uid)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    event.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("dlq_event_resolved", event_id=event_id, order_id=event.order_id)
    return {"status": "resolved", "event_id": event_id}


@router.post("/{event_id}/retry")
async def retry_dlq_event(event_id: str, db: AsyncSession = Depends(get_db)):
    """Re-run the agent graph for a dead-letter event."""
    import time

    from app.agent.graph import process_webhook_event
    from app.agent.state import OrderExceptionState

    try:
        uid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid event_id format")

    event = await db.get(DeadLetterEvent, uid)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    state: OrderExceptionState = {
        "webhook_id": f"dlq-retry-{event.webhook_id}",
        "event_type": event.event_type,
        "order_id": event.order_id,
        "raw_payload": event.raw_payload or {},
        "exception_type": None,
        "routing_decision": None,
        "verification_passed": None,
        "fulfillment_held": None,
        "shadowed": None,
        "tool_calls_log": [],
        "llm_input_tokens": None,
        "llm_output_tokens": None,
        "error": None,
        "retry_count": 0,
        "processing_start_ms": int(time.time() * 1000),
        "messages": [],
    }

    try:
        await process_webhook_event(state)
        event.resolved_at = datetime.now(timezone.utc)
        event.retry_count += 1
        event.last_attempt_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "retried_successfully", "event_id": event_id}
    except Exception as exc:
        event.retry_count += 1
        event.last_attempt_at = datetime.now(timezone.utc)
        event.error_message = str(exc)
        await db.commit()
        logger.error("dlq_retry_failed", event_id=event_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Retry failed: {exc}")
