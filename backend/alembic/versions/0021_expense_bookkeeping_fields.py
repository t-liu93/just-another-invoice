"""expense bookkeeping fields: paid_by, business_percentage, depreciation_years (M8.5 step 1)

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-14

Adds three bookkeeping columns to both ``expense`` and ``recurring_expense``:
- ``paid_by``              TEXT NOT NULL DEFAULT 'BUSINESS'
                           (PaidBy enum: PRIVATE | BUSINESS; pure indicator, D2)
- ``business_percentage``  NUMERIC(6,3) NOT NULL DEFAULT 100
                           (business-use %, 0–100; M10 will use for proration, D3)
- ``depreciation_years``   INTEGER NOT NULL DEFAULT 1
                           (≥ 1; 1 = fully expensed this year; amortisation in M10, D4)

This is a purely **additive** migration:
- No existing columns, tables, or indexes are modified.
- NOT NULL + server_default causes Postgres to back-fill all existing rows with
  BUSINESS / 100 / 1 automatically.
- ``downgrade`` drops only these six columns.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- expense table: add three bookkeeping columns -------------------------
    op.execute(
        """
        ALTER TABLE expense
            ADD COLUMN IF NOT EXISTS paid_by TEXT NOT NULL DEFAULT 'BUSINESS',
            ADD COLUMN IF NOT EXISTS business_percentage NUMERIC(6, 3) NOT NULL DEFAULT 100,
            ADD COLUMN IF NOT EXISTS depreciation_years INTEGER NOT NULL DEFAULT 1
        """
    )

    # -- recurring_expense table: same three columns (parity, D8) -------------
    op.execute(
        """
        ALTER TABLE recurring_expense
            ADD COLUMN IF NOT EXISTS paid_by TEXT NOT NULL DEFAULT 'BUSINESS',
            ADD COLUMN IF NOT EXISTS business_percentage NUMERIC(6, 3) NOT NULL DEFAULT 100,
            ADD COLUMN IF NOT EXISTS depreciation_years INTEGER NOT NULL DEFAULT 1
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE recurring_expense
            DROP COLUMN IF EXISTS depreciation_years,
            DROP COLUMN IF EXISTS business_percentage,
            DROP COLUMN IF EXISTS paid_by
        """
    )
    op.execute(
        """
        ALTER TABLE expense
            DROP COLUMN IF EXISTS depreciation_years,
            DROP COLUMN IF EXISTS business_percentage,
            DROP COLUMN IF EXISTS paid_by
        """
    )
