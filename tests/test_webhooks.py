"""Tests for the Shopify webhook endpoint signature verification and idempotency."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.services.idempotency import IdempotencyService
from tests.conftest import TEST_WEBHOOK_SECRET, make_webhook_signature

SAMPLE_ORDER = {
    "id": 12345,
    "order_number": 1001,
    "email": "customer@example.com",
    "tags": "",
    "financial_status": "paid",
    "fulfillment_status": None,
    "total_price": "99.00",
    "line_items": [],
    "shipping_address": {"country": "US", "address1": "123 Main St", "city": "NYC", "zip": "10001"},
    "risk_level": "LOW",
}


@pytest.fixture
def app_with_mocks(test_settings, redis_client):
    """Create a FastAPI TestClient with mocked dependencies."""
    from app.config import get_settings
    from app.main import app
    from app.services.idempotency import IdempotencyService

    app.dependency_overrides[get_settings] = lambda: test_settings

    # Inject a real fakeredis-backed idempotency service
    idempotency = IdempotencyService(redis_client)

    import app.main as main_module
    original_state_idempotency = getattr(app.state, "idempotency", None)
    app.state.idempotency = idempotency

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    app.dependency_overrides.clear()
    if original_state_idempotency:
        app.state.idempotency = original_state_idempotency


def test_valid_webhook_accepted(valid_webhook_headers):
    """A webhook with a valid HMAC signature should return 200."""
    body = json.dumps(SAMPLE_ORDER).encode()
    headers = valid_webhook_headers(SAMPLE_ORDER, webhook_id=str(uuid.uuid4()))

    from app.middleware.verify_webhook import verify_shopify_webhook
    from fastapi import Request
    import asyncio

    # Test the verify dependency directly
    async def _run():
        from unittest.mock import MagicMock
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/webhooks/orders/create",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)

        settings = Settings(
            shopify_webhook_secret=TEST_WEBHOOK_SECRET,
            app_env="test",
        )
        result = await verify_shopify_webhook(request, settings)
        return result

    result = asyncio.run(_run())
    assert result["id"] == SAMPLE_ORDER["id"]


def test_invalid_signature_returns_401(valid_webhook_headers):
    """A webhook with a tampered signature should be rejected with 401."""
    from app.middleware.verify_webhook import verify_shopify_webhook
    from fastapi import HTTPException, Request
    import asyncio

    body = json.dumps(SAMPLE_ORDER).encode()
    bad_headers = {
        "X-Shopify-Hmac-Sha256": "invalidsignature==",
        "X-Shopify-Webhook-Id": "test-001",
    }

    async def _run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in bad_headers.items()],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)
        settings = Settings(shopify_webhook_secret=TEST_WEBHOOK_SECRET, app_env="test")
        try:
            await verify_shopify_webhook(request, settings)
            return None
        except HTTPException as exc:
            return exc

    exc = asyncio.run(_run())
    assert exc is not None
    assert exc.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_webhook_skipped():
    """A duplicate webhook_id should be skipped (idempotency guard)."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = IdempotencyService(redis)

    webhook_id = "duplicate-test-001"
    first = await svc.mark_processed(webhook_id)
    second = await svc.mark_processed(webhook_id)

    assert first is True
    assert second is False

    await redis.aclose()


@pytest.mark.asyncio
async def test_unique_webhooks_both_processed():
    """Two distinct webhook IDs should both be marked as new."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = IdempotencyService(redis)

    first = await svc.mark_processed("webhook-aaa")
    second = await svc.mark_processed("webhook-bbb")

    assert first is True
    assert second is True

    await redis.aclose()
