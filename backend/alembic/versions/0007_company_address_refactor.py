"""company address refactor – structured fields replacing address_line1/2

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-09

M3 step 3: Align company address with the structured European/Dutch schema used
by the customer Address table.

Changes:
  - Add columns: street, house_number, house_number_addition, province
  - Copy address_line1 → street (best-effort; address_line2 is discarded)
  - Drop columns: address_line1, address_line2
  - postal_code / city / country_code already exist and are kept unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # noqa: F401 (used by alembic runtime)

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new structured address columns.
    op.add_column("company", sa.Column("street", sa.Text(), nullable=True))
    op.add_column("company", sa.Column("house_number", sa.Text(), nullable=True))
    op.add_column("company", sa.Column("house_number_addition", sa.Text(), nullable=True))
    op.add_column("company", sa.Column("province", sa.Text(), nullable=True))

    # Best-effort: copy address_line1 → street.
    op.execute("UPDATE company SET street = address_line1 WHERE address_line1 IS NOT NULL")

    # Drop obsolete columns.
    op.drop_column("company", "address_line1")
    op.drop_column("company", "address_line2")


def downgrade() -> None:
    # Restore old columns (data loss: street → address_line1 only).
    op.add_column("company", sa.Column("address_line1", sa.Text(), nullable=True))
    op.add_column("company", sa.Column("address_line2", sa.Text(), nullable=True))
    op.execute("UPDATE company SET address_line1 = street WHERE street IS NOT NULL")
    op.drop_column("company", "province")
    op.drop_column("company", "house_number_addition")
    op.drop_column("company", "house_number")
    op.drop_column("company", "street")
