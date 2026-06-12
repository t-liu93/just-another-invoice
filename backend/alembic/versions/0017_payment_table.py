"""payment table (M7 step 1)

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-12

Adds:
- ``payment`` table (sub-record of invoice; company-scoped; ON DELETE CASCADE
  from invoice; payment_method FK SET NULL; creator FK SET NULL)
- Indexes on company_id / invoice_id / payment_date

No new enum types – reuses InvoicePaidStatus / InvoiceStatus from M5.
Does not modify the ``invoice`` table (due_amount / paid_status / status
columns already exist from M5).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE payment (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id              UUID            NOT NULL,
            invoice_id              UUID            NOT NULL,
            payment_date            DATE            NOT NULL,
            amount                  NUMERIC(18, 3)  NOT NULL,
            base_amount             NUMERIC(18, 3)  NOT NULL,
            currency                VARCHAR(3)      NOT NULL,
            exchange_rate           NUMERIC(18, 8)  NOT NULL DEFAULT 1,
            payment_method_id       UUID,
            payment_method_name     TEXT,
            reference               TEXT,
            note                    TEXT,
            creator_id              UUID,
            created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT pk_payment PRIMARY KEY (id),
            CONSTRAINT fk_payment_company
                FOREIGN KEY (company_id)
                REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_payment_invoice
                FOREIGN KEY (invoice_id)
                REFERENCES invoice(id) ON DELETE CASCADE,
            CONSTRAINT fk_payment_payment_method
                FOREIGN KEY (payment_method_id)
                REFERENCES payment_method(id) ON DELETE SET NULL,
            CONSTRAINT fk_payment_creator
                FOREIGN KEY (creator_id)
                REFERENCES "user"(id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_payment_company_id   ON payment (company_id)")
    op.execute("CREATE INDEX ix_payment_invoice_id   ON payment (invoice_id)")
    op.execute("CREATE INDEX ix_payment_payment_date ON payment (payment_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payment")
