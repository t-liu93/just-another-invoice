"""Add UNIQUE partial index on product(company_id, sku) (M4 rework)

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-10

Replaces the plain index ix_product_sku with a UNIQUE partial index so the
SKU-based upsert contract is enforced at the database level.

Note: will fail if the product table already contains rows with duplicate
(company_id, sku) pairs; deduplicate manually before applying in that case.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_product_sku")
    op.execute(
        "CREATE UNIQUE INDEX uq_product_company_sku"
        " ON product (company_id, sku)"
        " WHERE sku IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_product_company_sku")
    op.execute(
        "CREATE INDEX ix_product_sku ON product (company_id, sku) WHERE sku IS NOT NULL"
    )
