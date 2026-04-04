import asyncio
import random
from datetime import datetime, timezone
from typing import Literal

import httpx
import structlog

logger = structlog.get_logger()


class ThreePLClient:
    MAX_RETRIES = 3

    def __init__(self, webhook_url: str, api_key: str):
        self._webhook_url = webhook_url
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=15.0)

    async def notify_order_exception(
        self,
        order_id: str,
        reason: str,
        urgency: Literal["low", "medium", "high"] = "medium",
    ) -> bool:
        """Notify 3PL of an order exception. Returns True on success.

        Retries up to MAX_RETRIES times with exponential backoff on 5xx errors.
        Never raises — returns False on permanent failure.
        """
        if not self._webhook_url:
            logger.warning("threpl_webhook_url_not_configured")
            return False

        payload = {
            "order_id": order_id,
            "reason": reason,
            "urgency": urgency,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await self._client.post(
                    self._webhook_url, json=payload, headers=headers
                )
                if resp.status_code < 500:
                    resp.raise_for_status()
                    logger.info("threpl_notified", order_id=order_id, urgency=urgency)
                    return True
                # 5xx — retry
                backoff = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "threpl_5xx_retry",
                    order_id=order_id,
                    status=resp.status_code,
                    attempt=attempt + 1,
                    backoff=round(backoff, 2),
                )
                await asyncio.sleep(backoff)
            except httpx.RequestError as exc:
                backoff = (2 ** attempt) + random.uniform(0, 1)
                logger.error(
                    "threpl_request_error",
                    order_id=order_id,
                    error=str(exc),
                    attempt=attempt + 1,
                )
                await asyncio.sleep(backoff)

        logger.error("threpl_notification_failed", order_id=order_id, retries=self.MAX_RETRIES)
        return False

    async def close(self):
        await self._client.aclose()
