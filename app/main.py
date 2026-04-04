import asyncio
import structlog
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.session import init_db, AsyncSessionLocal


logger = structlog.get_logger()


def _init_sentry(settings) -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            traces_sample_rate=0.2,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        logger.info("sentry_initialized", env=settings.app_env)
    except ImportError:
        logger.warning("sentry_sdk_not_installed", hint="pip install sentry-sdk[fastapi]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _init_sentry(settings)

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
    from app.services.event_router import EventRouter, NotificationWorker
    from app.agent.tools import inject_tool_dependencies
    from app.agent.nodes import inject_event_router

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

    app.state.event_router = EventRouter(app.state.redis)
    inject_event_router(app.state.event_router)

    inject_tool_dependencies(
        shopify=app.state.shopify,
        threpl=app.state.threpl,
        db_factory=AsyncSessionLocal,
    )

    # Start the notification worker as a background asyncio task
    worker = NotificationWorker(app.state.redis, app.state.slack)
    app.state.notification_task = asyncio.create_task(
        worker.run(settings), name="notification-worker"
    )

    # Start weekly report scheduler
    from app.services.weekly_report import start_weekly_report_scheduler
    app.state.weekly_report_task = asyncio.create_task(
        start_weekly_report_scheduler(app), name="weekly-report-scheduler"
    )

    logger.info("startup_complete", env=settings.app_env, sandbox=settings.is_sandbox)
    yield

    app.state.notification_task.cancel()
    app.state.weekly_report_task.cancel()
    try:
        await asyncio.gather(
            app.state.notification_task,
            app.state.weekly_report_task,
            return_exceptions=True,
        )
    except asyncio.CancelledError:
        pass

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


from app.routers import webhooks, dlq, admin, dashboard  # noqa: E402

app.include_router(webhooks.router)
app.include_router(dlq.router)
app.include_router(admin.router)
app.include_router(dashboard.router)


@app.get("/health")
async def health(request: Request):
    from datetime import datetime, timezone
    from app.services.event_router import NotificationWorker

    checks: dict[str, str] = {}

    # Redis connectivity
    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    # Notification worker heartbeat
    try:
        raw = await request.app.state.redis.get(NotificationWorker.HEARTBEAT_KEY)
        if raw:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(raw)).total_seconds()
            checks["worker"] = "ok" if age < 600 else f"stale:{age:.0f}s"
        else:
            checks["worker"] = "not_started"
    except Exception as exc:
        checks["worker"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())

    if not all_ok:
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                "order-exception-agent health degraded",
                level="critical",
                extras={"checks": checks},
            )
        except ImportError:
            pass

    status_code = 200 if all_ok else 503
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "degraded", "service": "shopify-order-exception-agent", "checks": checks},
    )
