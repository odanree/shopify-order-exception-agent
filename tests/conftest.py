"""Pytest fixtures for the order exception agent tests."""
import base64
import hashlib
import hmac
import json

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, init_db
import app.models.db  # noqa: F401

TEST_WEBHOOK_SECRET = "test-webhook-secret-abc123"
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def test_settings():
    return Settings(
        shopify_shop_domain="test-store.myshopify.com",
        shopify_access_token="test-token",
        shopify_webhook_secret=TEST_WEBHOOK_SECRET,
        anthropic_api_key="sk-ant-test",
        redis_url="redis://localhost:6379/0",
        database_url=TEST_DATABASE_URL,
        slack_webhook_url="https://hooks.slack.com/test",
        slack_default_channel="#test",
        threpl_webhook_url="https://3pl.test/webhook",
        threpl_api_key="3pl-key",
        app_env="test",
    )


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


def make_webhook_signature(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@pytest.fixture
def valid_webhook_headers():
    """Return a factory that generates valid Shopify webhook headers for a given payload."""

    def _make(payload: dict, webhook_id: str = "test-webhook-001") -> dict:
        body = json.dumps(payload).encode()
        sig = make_webhook_signature(body)
        return {
            "Content-Type": "application/json",
            "X-Shopify-Hmac-Sha256": sig,
            "X-Shopify-Webhook-Id": webhook_id,
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Shop-Domain": "test-store.myshopify.com",
        }

    return _make
