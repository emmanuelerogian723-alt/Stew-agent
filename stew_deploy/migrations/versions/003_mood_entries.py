"""Add mood_entries table for Mood DNA feature

Revision ID: 003
Revises: 002
Create Date: 2026-08-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mood_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("mood", sa.String(30), nullable=False),
        sa.Column("mood_score", sa.Integer, server_default="50"),
        sa.Column("energy_score", sa.Integer, server_default="50"),
        sa.Column("message_snippet", sa.String(200), nullable=True),
        sa.Column("day_of_week", sa.Integer, server_default="0"),
        sa.Column("hour_of_day", sa.Integer, server_default="12"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_mood_entries_created_at", "mood_entries", ["created_at"])


def downgrade() -> None:
    op.drop_table("mood_entries")
