"""number_sequence table + customer.invoice_prefix (M5 step 2)

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-10

Adds:
- ``number_sequence`` table with COMPANY/CUSTOMER scope, partial unique indexes,
  and a check constraint on ``next_value >= 1``.
- ``customer.invoice_prefix`` column (TEXT, nullable) for the
  ``{{CUSTOMER_SERIES}}`` numbering placeholder.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create PostgreSQL enum for sequence scope (idempotent).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE numbersequencescope AS ENUM ('COMPANY', 'CUSTOMER');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE number_sequence (
            id            UUID                  NOT NULL DEFAULT gen_random_uuid(),
            company_id    UUID                  NOT NULL,
            document_type TEXT                  NOT NULL,
            scope         numbersequencescope   NOT NULL,
            customer_id   UUID,
            next_value    BIGINT                NOT NULL DEFAULT 1,
            PRIMARY KEY (id),
            CONSTRAINT fk_number_sequence_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_number_sequence_customer
                FOREIGN KEY (customer_id) REFERENCES customer(id) ON DELETE CASCADE,
            CONSTRAINT ck_number_sequence_next_value_ge1
                CHECK (next_value >= 1),
            CONSTRAINT ck_number_sequence_customer_id
                CHECK (
                    (scope = 'CUSTOMER' AND customer_id IS NOT NULL)
                    OR (scope = 'COMPANY' AND customer_id IS NULL)
                )
        )
        """
    )

    op.execute("CREATE INDEX ix_number_sequence_company_id ON number_sequence (company_id)")

    # Partial unique index: one COMPANY-scope row per (company, document_type)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_number_sequence_company_scope
        ON number_sequence (company_id, document_type)
        WHERE scope = 'COMPANY'
        """
    )

    # Partial unique index: one CUSTOMER-scope row per (company, document_type, customer)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_number_sequence_customer_scope
        ON number_sequence (company_id, document_type, customer_id)
        WHERE scope = 'CUSTOMER'
        """
    )

    # Add invoice_prefix to customer table
    op.execute("ALTER TABLE customer ADD COLUMN invoice_prefix TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE customer DROP COLUMN IF EXISTS invoice_prefix")
    op.execute("DROP TABLE IF EXISTS number_sequence")
    op.execute("DROP TYPE IF EXISTS numbersequencescope")
