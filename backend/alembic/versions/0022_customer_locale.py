"""customer.locale column for per-customer document language (M9 step 2)

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-14

Adds a nullable ``locale`` column (String(5)) to the ``customer`` table.
NULL = follow company default; "en" / "zh" = explicit override.

This is a purely additive migration:
- No existing columns are modified.
- NULL is the natural default for all existing rows (meaning "follow company
  default"), so no back-fill is needed.
- ``downgrade`` drops only the new column.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE customer
            ADD COLUMN IF NOT EXISTS locale VARCHAR(5) NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE customer
            DROP COLUMN IF EXISTS locale
        """
    )
