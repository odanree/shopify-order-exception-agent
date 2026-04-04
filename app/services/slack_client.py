from typing import Literal

import httpx
import structlog

logger = structlog.get_logger()

SEVERITY_COLORS = {
    "info": "#0070D2",
    "warning": "#FF7043",
    "critical": "#D32F2F",
}


class SlackClient:
    def __init__(self, webhook_url: str):
        self._webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send_alert(
        self,
        channel: str,
        order_id: str,
        message: str,
        severity: Literal["info", "warning", "critical"] = "info",
    ) -> bool:
        """Post a Slack Block Kit message. Returns True on success, False on failure (never raises)."""
        if not self._webhook_url:
            logger.warning("slack_webhook_url_not_configured")
            return False

        color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["info"])
        payload = {
            "channel": channel,
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"[{severity.upper()}] Order Exception — #{order_id}",
                            },
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": message},
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"Order ID: `{order_id}` | Severity: *{severity}*",
                                }
                            ],
                        },
                    ],
                }
            ],
        }

        try:
            resp = await self._client.post(self._webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("slack_alert_sent", order_id=order_id, severity=severity)
            return True
        except Exception as exc:
            logger.error("slack_alert_failed", order_id=order_id, error=str(exc))
            return False

    async def close(self):
        await self._client.aclose()
