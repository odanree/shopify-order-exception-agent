import structlog

logger = structlog.get_logger()


class IdempotencyService:
    TTL_SECONDS = 604800  # 7 days
    KEY_PREFIX = "shopify:webhook:"

    def __init__(self, redis_client):
        self.redis = redis_client

    async def mark_processed(self, webhook_id: str) -> bool:
        """Atomically check and mark a webhook as processed.

        Returns True if this is a NEW webhook (should be processed).
        Returns False if already seen (duplicate — skip processing).
        """
        key = f"{self.KEY_PREFIX}{webhook_id}"
        result = await self.redis.set(key, "1", nx=True, ex=self.TTL_SECONDS)
        if result is None:
            logger.info("webhook_duplicate_skipped", webhook_id=webhook_id)
            return False
        return True
