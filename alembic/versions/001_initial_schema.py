"""Initial schema — audit_logs and dead_letter_events tables.

Revision ID: 001
Revises:
Create Date: 2026-04-08

Uses CREATE TABLE IF NOT EXISTS so it is safe to run against a database that
was bootstrapped via SQLAlchemy create_all() before Alembic was introduced.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            webhook_id VARCHAR(128) NOT NULL,
            order_id VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            exception_type VARCHAR(64),
            action_taken VARCHAR(128),
            tool_calls JSONB,
            metadata JSONB,
            processing_time_ms INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_webhook_id ON audit_logs (webhook_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_order_id ON audit_logs (order_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS dead_letter_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            webhook_id VARCHAR(128) NOT NULL,
            order_id VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            raw_payload JSONB,
            error_message TEXT,
            error_traceback TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dead_letter_events_webhook_id ON dead_letter_events (webhook_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dead_letter_events_order_id ON dead_letter_events (order_id)")


def downgrade() -> None:
    op.drop_table("dead_letter_events")
    op.drop_table("audit_logs")
