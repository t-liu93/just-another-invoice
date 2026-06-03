"""create setting table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setting",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "level",
            sa.Text,
            nullable=False,
            index=True,
        ),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "key",
            sa.Text,
            nullable=False,
            index=True,
        ),
        sa.Column(
            "value",
            postgresql.JSONB,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Use NULLS NOT DISTINCT so that (level, NULL, key) rows are treated as
    # duplicates — required for upserts on GLOBAL-level settings where
    # scope_id is NULL.  Supported since PostgreSQL 15+.
    op.execute(
        "CREATE UNIQUE INDEX uq_setting_level_scope_key "
        "ON setting (level, scope_id, key) NULLS NOT DISTINCT"
    )
    # Enforce valid level/scope_id combinations:
    #   GLOBAL  → scope_id IS NULL
    #   COMPANY/USER → scope_id IS NOT NULL
    op.execute(
        "ALTER TABLE setting ADD CONSTRAINT chk_setting_level_scope "
        "CHECK ("
        "  (level = 'GLOBAL' AND scope_id IS NULL) OR "
        "  (level IN ('COMPANY', 'USER') AND scope_id IS NOT NULL)"
        ")"
    )


def downgrade() -> None:
    op.drop_table("setting")
