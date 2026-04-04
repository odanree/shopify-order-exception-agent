import structlog
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.session import init_db, AsyncSessionLocal


logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.app_env == "development"
            else structlog.processors.JSONRenderer(),
        ]
    )

    # Redis
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    # Database
    await init_db()

    # Service clients
    from app.services.shopify_client import ShopifyGraphQLClient
    from app.services.idempotency import IdempotencyService
    from app.services.slack_client import SlackClient
    from app.services.threpl_client import ThreePLClient
    from app.agent.tools import inject_tool_dependencies

    app.state.shopify = ShopifyGraphQLClient(
        domain=settings.active_shopify_domain,
        token=settings.active_shopify_token,
        redis_client=app.state.redis,
    )
    app.state.idempotency = IdempotencyService(app.state.redis)
    app.state.slack = SlackClient(settings.slack_webhook_url)
    app.state.threpl = ThreePLClient(
        webhook_url=settings.threpl_webhook_url,
        api_key=settings.threpl_api_key,
    )

    inject_tool_dependencies(
        shopify=app.state.shopify,
        slack=app.state.slack,
        threpl=app.state.threpl,
        db_factory=AsyncSessionLocal,
    )

    logger.info("startup_complete", env=settings.app_env, sandbox=settings.is_sandbox)
    yield

    await app.state.shopify.close()
    await app.state.redis.aclose()
    logger.info("shutdown_complete")


app = FastAPI(title="Shopify Order Exception Agent", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def sandbox_header_middleware(request: Request, call_next):
    response = await call_next(request)
    if get_settings().is_sandbox:
        response.headers["X-Sandbox"] = "true"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=str(request.url.path), error=str(exc), exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


from app.routers import webhooks, dlq  # noqa: E402

app.include_router(webhooks.router)
app.include_router(dlq.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "shopify-order-exception-agent"}
