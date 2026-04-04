"""Redis pub/sub event router for decoupled Slack notifications (clawhip pattern).

The agent emits structured events to Redis pub/sub; the NotificationWorker subscribes
and delivers them to Slack. This keeps notification side-effects out of the LLM
context window and agent state.
"""
import asyncio
import json
import structlog
from datetime import datetime, timezone

logger = structlog.get_logger()

NOTIFICATIONS_CHANNEL = "shopify:events:order-notifications"


class EventRouter:
    """Publish structured events to a Redis pub/sub channel. Fire-and-forget."""

    def __init__(self, redis_client):
        self._redis = redis_client

    async def emit(self, event_type: str, payload: dict) -> None:
        """Publish an event. Never raises — failures are logged and dropped."""
        try:
            event = {
                "type": event_type,
                "payload": payload,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            await self._redis.publish(NOTIFICATIONS_CHANNEL, json.dumps(event))
        except Exception as exc:
            logger.error("event_emit_failed", event_type=event_type, error=str(exc))


class NotificationWorker:
    """Asyncio daemon: subscribe to NOTIFICATIONS_CHANNEL, dispatch to Slack."""

    def __init__(self, redis_client, slack_client):
        self._redis = redis_client
        self._slack = slack_client

    async def run(self, settings) -> None:
        """Subscribe and dispatch in a crash-retry loop. Runs until cancelled."""
        while True:
            try:
                await self._subscribe_loop(settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("notification_worker_crashed", error=str(exc))
                await asyncio.sleep(5)

    async def _subscribe_loop(self, settings) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(NOTIFICATIONS_CHANNEL)
        logger.info("notification_worker_started", channel=NOTIFICATIONS_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    await self._dispatch(event, settings)
                except Exception as exc:
                    logger.error("notification_dispatch_failed", error=str(exc))
        finally:
            await pubsub.unsubscribe(NOTIFICATIONS_CHANNEL)
            await pubsub.aclose()

    async def _dispatch(self, event: dict, settings) -> None:
        event_type = event.get("type")
        payload = event.get("payload", {})
        if event_type == "order_exception_alert":
            await self._slack.send_alert(
                channel=payload.get("channel", settings.slack_default_channel),
                order_id=payload.get("order_id", "unknown"),
                message=payload.get("message", ""),
                severity=payload.get("severity", "info"),
            )
        else:
            logger.warning("notification_unknown_event_type", event_type=event_type)
