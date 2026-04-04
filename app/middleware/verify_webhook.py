import base64
import hashlib
import hmac
import json

import structlog
from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings

logger = structlog.get_logger()


async def verify_shopify_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """FastAPI dependency that validates the Shopify HMAC-SHA256 webhook signature.

    Returns the parsed JSON body dict on success. Raises 401 on signature mismatch.
    """
    raw_body = await request.body()

    signature_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
    if not signature_header:
        logger.warning(
            "webhook_missing_signature",
            path=str(request.url),
            shop=request.headers.get("X-Shopify-Shop-Domain", "unknown"),
        )
        raise HTTPException(status_code=401, detail="Missing webhook signature")

    secret = settings.shopify_webhook_secret.encode("utf-8")
    computed_digest = hmac.new(secret, raw_body, hashlib.sha256).digest()
    computed_b64 = base64.b64encode(computed_digest).decode()

    if not hmac.compare_digest(computed_b64, signature_header):
        logger.warning(
            "webhook_invalid_signature",
            shop=request.headers.get("X-Shopify-Shop-Domain", "unknown"),
            topic=request.headers.get("X-Shopify-Topic", "unknown"),
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return json.loads(raw_body)
