"""address table + addresstype enum

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-09

Creates the ``address`` table for customer billing/shipping addresses (M3 step 2).
Also creates the ``addresstype`` PostgreSQL enum (BILLING / SHIPPING).
Constraint: UNIQUE(customer_id, type) ensures at most one address per type per customer.
FK: customer_id → customer.id ON DELETE CASCADE (red-line 3).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use raw SQL to avoid SQLAlchemy's Enum._on_table_create hook, which
    # ignores create_type=False in some SQLAlchemy+asyncpg version combos and
    # tries to CREATE TYPE even after we already created it via the DO block.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE addresstype AS ENUM ('BILLING', 'SHIPPING');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TABLE address (
            id          UUID         NOT NULL DEFAULT gen_random_uuid(),
            customer_id UUID         NOT NULL,
            type        addresstype  NOT NULL,
            street      TEXT,
            house_number            TEXT,
            house_number_addition   TEXT,
            postal_code TEXT,
            city        TEXT,
            province    TEXT,
            country_code CHAR(2),
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_address_customer_id
                FOREIGN KEY (customer_id) REFERENCES customer(id) ON DELETE CASCADE,
            CONSTRAINT uq_address_customer_type
                UNIQUE (customer_id, type)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_address_customer_id ON address (customer_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS address")
    op.execute("DROP TYPE IF EXISTS addresstype")
