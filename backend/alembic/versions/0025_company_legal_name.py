"""company.legal_name – optional registered legal name for trade-name disclosure (legal-name step 1)

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-29

Adds a nullable ``legal_name`` column (TEXT) to the ``company`` table.
NULL = no legal name set; the trade-name disclosure line is suppressed.

This is a purely additive migration:
- No existing columns are modified.
- NULL is the natural default for all existing rows.
- ``downgrade`` drops only the new column.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE company
            ADD COLUMN IF NOT EXISTS legal_name TEXT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE company
            DROP COLUMN IF EXISTS legal_name
        """
    )
