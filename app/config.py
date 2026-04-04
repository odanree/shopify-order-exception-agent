from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Shopify (production)
    shopify_shop_domain: str = "example.myshopify.com"
    shopify_access_token: str = ""
    shopify_webhook_secret: str = ""
    shopify_api_version: str = "2024-01"

    # Shopify sandbox / dev store
    shopify_sandbox_mode: bool = False
    shopify_dev_store_domain: str = ""
    shopify_dev_access_token: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-4-6"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL
    database_url: str = (
        "postgresql+asyncpg://agent_user:secret@localhost:5432/order_exception_agent"
    )

    # Slack
    slack_webhook_url: str = ""
    slack_default_channel: str = "#ops-alerts"

    # 3PL
    threpl_webhook_url: str = ""
    threpl_api_key: str = ""

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    port: int = 8000

    @property
    def is_sandbox(self) -> bool:
        return self.shopify_sandbox_mode

    @property
    def active_shopify_domain(self) -> str:
        return self.shopify_dev_store_domain if self.is_sandbox else self.shopify_shop_domain

    @property
    def active_shopify_token(self) -> str:
        return self.shopify_dev_access_token if self.is_sandbox else self.shopify_access_token


@lru_cache
def get_settings() -> Settings:
    return Settings()
