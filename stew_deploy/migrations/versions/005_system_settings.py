"""Add system_settings table for runtime channel configuration.

Revision ID: 005_system_settings
Revises: 004_scheduled_tasks
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa


revision = "005_system_settings"
down_revision = "004_scheduled_tasks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("system_settings")
