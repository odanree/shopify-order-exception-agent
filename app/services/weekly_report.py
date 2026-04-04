"""Weekly impact report for the order exception agent.

Aggregates 7-day stats from Postgres and delivers a Slack message every Monday.
No external dependencies — uses the existing SlackClient and DB session factory.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

logger = structlog.get_logger()

_SEVEN_DAYS = timedelta(days=7)
_LAST_SENT_KEY = "agent:weekly_report:last_sent"  # Redis key (date string YYYY-MM-DD)


async def generate_report_text(db_factory) -> str:
    """Query Postgres and return a formatted Slack message string."""
    from app.models.db import AuditLog, DeadLetterEvent

    seven_days_ago = datetime.now(timezone.utc) - _SEVEN_DAYS

    async with db_factory() as session:
        total = await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.created_at >= seven_days_ago)
        ) or 0
        dead_letters = await session.scalar(
            select(func.count(DeadLetterEvent.id)).where(DeadLetterEvent.resolved_at.is_(None))
        ) or 0
        avg_ms = await session.scalar(
            select(func.avg(AuditLog.processing_time_ms)).where(
                AuditLog.created_at >= seven_days_ago
            )
        ) or 0

        # Count fulfillment holds applied
        holds = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.created_at >= seven_days_ago,
                AuditLog.exception_type.in_(["fraud_risk", "address_invalid", "payment_issue"]),
            )
        ) or 0

        # Top exception type
        top_row = (
            await session.execute(
                select(AuditLog.exception_type, func.count(AuditLog.id).label("cnt"))
                .where(AuditLog.created_at >= seven_days_ago)
                .group_by(AuditLog.exception_type)
                .order_by(func.count(AuditLog.id).desc())
                .limit(1)
            )
        ).first()

    hours_saved = round(total * 8 / 60, 1)
    success_rate = round((total - dead_letters) / max(total, 1) * 100, 1)
    top_type = (top_row[0] or "none").replace("_", " ").title() if top_row else "—"

    week_start = (datetime.now(timezone.utc) - _SEVEN_DAYS).strftime("%b %d")
    week_end = datetime.now(timezone.utc).strftime("%b %d")

    return (
        f"📊 *Order Exception Agent — Weekly Impact Report*\n"
        f"_{week_start} – {week_end}_\n\n"
        f"• *{total}* order exceptions handled automatically\n"
        f"• *{hours_saved}h* of manual triage saved (@ 8 min/event)\n"
        f"• *{holds}* fulfillment holds applied (fraud, address, payment)\n"
        f"• *{success_rate}%* success rate | {dead_letters} in dead-letter queue\n"
        f"• Most common exception: *{top_type}*\n"
        f"• Avg processing time: *{round(avg_ms / 1000, 1)}s* per event\n\n"
        f"_View full dashboard → <http://localhost:8000/dashboard|Agent Dashboard>_"
    )


async def send_weekly_report(db_factory, slack_client, settings) -> None:
    """Generate and deliver the weekly report to the configured Slack channel."""
    try:
        text = await generate_report_text(db_factory)
        await slack_client.send_alert(
            channel=settings.slack_default_channel,
            order_id="weekly-report",
            message=text,
            severity="info",
        )
        logger.info("weekly_report_sent", channel=settings.slack_default_channel)
    except Exception as exc:
        logger.error("weekly_report_failed", error=str(exc))


async def start_weekly_report_scheduler(app) -> None:
    """Asyncio task: send report every Monday at ~8:00 AM UTC.

    Uses Redis to guard against duplicate sends on the same day
    (e.g. if the service restarts on Monday morning).
    """
    from app.config import get_settings
    from app.db.session import AsyncSessionLocal

    settings = get_settings()

    while True:
        try:
            now = datetime.now(timezone.utc)
            # Monday = weekday 0
            if now.weekday() == 0 and now.hour >= 8:
                today_str = now.strftime("%Y-%m-%d")
                redis = app.state.redis
                last_sent = await redis.get(_LAST_SENT_KEY)
                if last_sent != today_str:
                    await send_weekly_report(AsyncSessionLocal, app.state.slack, settings)
                    await redis.set(_LAST_SENT_KEY, today_str, ex=90000)  # 25h TTL
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("weekly_report_scheduler_error", error=str(exc))

        # Check every hour
        await asyncio.sleep(3600)
