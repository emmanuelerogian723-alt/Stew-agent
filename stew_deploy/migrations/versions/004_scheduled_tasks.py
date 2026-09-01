"""Add scheduled_tasks table for the Stew Scheduler engine.

Revision ID: 004_scheduled_tasks
Revises: 003
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "004_scheduled_tasks"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("schedule_type", sa.String(20), nullable=False),
        sa.Column("schedule_config", sa.String(100), nullable=False),
        sa.Column("delivery_method", sa.String(50), server_default="telegram"),
        sa.Column("delivery_target", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("1")),
        sa.Column("last_run_at", sa.DateTime, nullable=True),
        sa.Column("next_run_at", sa.DateTime, nullable=True, index=True),
        sa.Column("last_result", sa.Text, nullable=True),
        sa.Column("run_count", sa.Integer, server_default="0"),
        sa.Column("max_runs", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("scheduled_tasks")
