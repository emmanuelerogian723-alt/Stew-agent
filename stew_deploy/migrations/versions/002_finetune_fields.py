"""Add fine-tune/persona fields to users + Mistral support

Revision ID: 002
Revises: 001
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add fine-tune and persona fields to users table
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("persona", sa.String(50), nullable=True, server_default="general"))
        batch_op.add_column(sa.Column("custom_instructions", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("persona_name", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("response_style", sa.String(20), nullable=True, server_default="balanced"))
        batch_op.add_column(sa.Column("language", sa.String(10), nullable=True, server_default="en"))
        batch_op.add_column(sa.Column("preferred_model", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("mistral_api_key", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("persona")
        batch_op.drop_column("custom_instructions")
        batch_op.drop_column("persona_name")
        batch_op.drop_column("response_style")
        batch_op.drop_column("language")
        batch_op.drop_column("preferred_model")
        batch_op.drop_column("mistral_api_key")
