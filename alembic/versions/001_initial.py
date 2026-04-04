"""Initial schema — audit_logs and dead_letter_events

Revision ID: 001
Revises:
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("webhook_id", sa.String(128), nullable=False, index=True),
        sa.Column("order_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("exception_type", sa.String(64), nullable=True),
        sa.Column("action_taken", sa.String(128), nullable=True),
        sa.Column("tool_calls", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("processing_time_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "dead_letter_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("webhook_id", sa.String(128), nullable=False, index=True),
        sa.Column("order_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("error_traceback", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, default=0, nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("dead_letter_events")
    op.drop_table("audit_logs")
