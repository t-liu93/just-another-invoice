"""quote-origin payments and VAT allocation snapshots

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-27

Existing payments remain invoice-only. No historical amount, state, date, or
VAT snapshot is recalculated.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE payment DROP CONSTRAINT fk_payment_invoice")
    op.execute("ALTER TABLE payment ALTER COLUMN invoice_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE payment ADD CONSTRAINT fk_payment_invoice "
        "FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE payment ADD COLUMN quote_id UUID")
    op.execute(
        "ALTER TABLE payment ADD CONSTRAINT fk_payment_quote "
        "FOREIGN KEY (quote_id) REFERENCES quote(id) ON DELETE RESTRICT"
    )
    op.execute("CREATE INDEX ix_payment_quote_id ON payment (quote_id)")
    op.execute(
        "ALTER TABLE payment ADD CONSTRAINT ck_payment_document_link "
        "CHECK (invoice_id IS NOT NULL OR quote_id IS NOT NULL)"
    )

    op.execute(
        """
        CREATE TABLE payment_tax (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            payment_id UUID NOT NULL,
            vat_rate_id UUID,
            vat_rate_label TEXT NOT NULL,
            vat_rate_percent NUMERIC(6, 3) NOT NULL,
            vat_treatment_code TEXT NOT NULL,
            vat_treatment_effect TEXT NOT NULL,
            vat_treatment_requires_icp BOOLEAN NOT NULL,
            taxable_amount NUMERIC(18, 3) NOT NULL,
            vat_amount NUMERIC(18, 3) NOT NULL,
            gross_amount NUMERIC(18, 3) NOT NULL,
            base_taxable_amount NUMERIC(18, 3) NOT NULL,
            base_vat_amount NUMERIC(18, 3) NOT NULL,
            base_gross_amount NUMERIC(18, 3) NOT NULL,
            bucket_key TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            CONSTRAINT pk_payment_tax PRIMARY KEY (id),
            CONSTRAINT fk_payment_tax_payment
                FOREIGN KEY (payment_id) REFERENCES payment(id) ON DELETE CASCADE,
            CONSTRAINT fk_payment_tax_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_payment_tax_payment_id ON payment_tax (payment_id)")


def downgrade() -> None:
    # This revision makes quote-origin payments and their VAT snapshots
    # representable.  Revision 0027 cannot represent either safely: silently
    # dropping them would corrupt tax history/provenance, while SET NOT NULL
    # would otherwise fail only after partial DDL.  Refuse before *any* DDL so
    # an operator can export/migrate the data deliberately before rollback.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM payment WHERE quote_id IS NOT NULL)
               OR EXISTS (SELECT 1 FROM payment_tax) THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0028: quote-origin payment provenance or '
                    'VAT snapshots exist. '
                    'Export or remove M11.5 quote-payment data before rolling back.';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TABLE IF EXISTS payment_tax")
    op.execute("ALTER TABLE payment DROP CONSTRAINT ck_payment_document_link")
    op.execute("DROP INDEX IF EXISTS ix_payment_quote_id")
    op.execute("ALTER TABLE payment DROP CONSTRAINT fk_payment_quote")
    op.execute("ALTER TABLE payment DROP COLUMN quote_id")
    op.execute("ALTER TABLE payment DROP CONSTRAINT fk_payment_invoice")
    op.execute("ALTER TABLE payment ALTER COLUMN invoice_id SET NOT NULL")
    op.execute(
        "ALTER TABLE payment ADD CONSTRAINT fk_payment_invoice "
        "FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE"
    )
