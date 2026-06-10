"""invoice / invoice_line / invoice_tax / invoice_line_tax tables (M5 step 3)

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-10

Adds:
- PG enum types for invoice status, paid status, tax mode
- ``invoice`` table with all fields, unique constraint, and indexes
- ``invoice_line`` table with cascade-delete FK to invoice
- ``invoice_tax`` table with cascade-delete FK to invoice
- ``invoice_line_tax`` table with cascade-delete FK to invoice_line
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Enum types -----------------------------------------------------------
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE invoicestatus AS ENUM ('DRAFT', 'SENT', 'COMPLETED', 'CANCELLED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE invoicepaidstatus AS ENUM ('UNPAID', 'PARTIALLY_PAID', 'PAID');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE invoicetaxmode AS ENUM ('LINE', 'DOCUMENT');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE discounttype AS ENUM ('NONE', 'PERCENTAGE', 'FIXED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    # -- invoice table --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE invoice (
            id                          UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id                  UUID            NOT NULL,
            customer_id                 UUID            NOT NULL,

            invoice_number              TEXT            NOT NULL,
            sequence_number             BIGINT          NOT NULL,
            customer_sequence_number    BIGINT,
            unique_hash                 TEXT,
            reference_number            TEXT,

            invoice_date                DATE            NOT NULL,
            due_date                    DATE,

            status                      invoicestatus   NOT NULL DEFAULT 'DRAFT',
            paid_status                 invoicepaidstatus NOT NULL DEFAULT 'UNPAID',

            currency                    CHAR(3)         NOT NULL,
            exchange_rate               NUMERIC(18,8)   NOT NULL DEFAULT 1,

            tax_mode                    invoicetaxmode  NOT NULL,
            amounts_include_vat         BOOLEAN         NOT NULL,
            vat_treatment_id            UUID            NOT NULL,
            document_vat_rate_id        UUID,

            vat_treatment_code          TEXT            NOT NULL,
            vat_treatment_label         TEXT            NOT NULL,
            vat_treatment_effect        TEXT            NOT NULL,
            vat_treatment_requires_icp  BOOLEAN         NOT NULL,

            discount_type               discounttype    NOT NULL DEFAULT 'NONE',
            discount_value              NUMERIC(10,3)   NOT NULL DEFAULT 0,
            document_discount_amount    NUMERIC(18,3)   NOT NULL DEFAULT 0,

            subtotal_excl_vat           NUMERIC(18,3)   NOT NULL,
            line_discount_total         NUMERIC(18,3)   NOT NULL,
            taxable_amount              NUMERIC(18,3)   NOT NULL,
            vat_total                   NUMERIC(18,3)   NOT NULL,
            total_incl_vat              NUMERIC(18,3)   NOT NULL,
            due_amount                  NUMERIC(18,3)   NOT NULL,

            base_subtotal_excl_vat      NUMERIC(18,3)   NOT NULL,
            base_line_discount_total    NUMERIC(18,3)   NOT NULL,
            base_taxable_amount         NUMERIC(18,3)   NOT NULL,
            base_vat_total              NUMERIC(18,3)   NOT NULL,
            base_total_incl_vat         NUMERIC(18,3)   NOT NULL,
            base_due_amount             NUMERIC(18,3)   NOT NULL,

            notes                       TEXT,
            creator_id                  UUID,

            created_at                  TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT now(),

            PRIMARY KEY (id),
            CONSTRAINT fk_invoice_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_invoice_customer
                FOREIGN KEY (customer_id) REFERENCES customer(id) ON DELETE RESTRICT,
            CONSTRAINT fk_invoice_vat_treatment
                FOREIGN KEY (vat_treatment_id) REFERENCES vat_treatment(id) ON DELETE RESTRICT,
            CONSTRAINT fk_invoice_document_vat_rate
                FOREIGN KEY (document_vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT,
            CONSTRAINT fk_invoice_creator
                FOREIGN KEY (creator_id) REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT uq_invoice_company_number
                UNIQUE (company_id, invoice_number)
        )
        """
    )
    op.execute("CREATE INDEX ix_invoice_company_id ON invoice (company_id)")
    op.execute("CREATE INDEX ix_invoice_customer_id ON invoice (customer_id)")
    op.execute("CREATE INDEX ix_invoice_invoice_date ON invoice (invoice_date)")

    # -- invoice_line table ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE invoice_line (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            invoice_id              UUID            NOT NULL,
            sort_order              INTEGER         NOT NULL DEFAULT 0,

            product_id              UUID,
            name                    TEXT            NOT NULL,
            description             TEXT,
            quantity                NUMERIC(18,3)   NOT NULL,
            unit_id                 UUID,
            unit_name               TEXT,

            unit_price              NUMERIC(18,3)   NOT NULL,
            discount_type           discounttype    NOT NULL DEFAULT 'NONE',
            discount_value          NUMERIC(10,3)   NOT NULL DEFAULT 0,
            vat_rate_id             UUID,

            vat_rate_label          TEXT,
            vat_rate_percent        NUMERIC(6,3),

            subtotal_excl_vat       NUMERIC(18,3)   NOT NULL,
            subtotal_incl_vat       NUMERIC(18,3)   NOT NULL,
            line_discount_amount    NUMERIC(18,3)   NOT NULL,
            document_discount_share NUMERIC(18,3)   NOT NULL,
            taxable_amount          NUMERIC(18,3)   NOT NULL,
            vat_total               NUMERIC(18,3)   NOT NULL,
            total_incl_vat          NUMERIC(18,3)   NOT NULL,

            PRIMARY KEY (id),
            CONSTRAINT fk_invoice_line_invoice
                FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE,
            CONSTRAINT fk_invoice_line_product
                FOREIGN KEY (product_id) REFERENCES product(id) ON DELETE SET NULL,
            CONSTRAINT fk_invoice_line_unit
                FOREIGN KEY (unit_id) REFERENCES unit(id) ON DELETE SET NULL,
            CONSTRAINT fk_invoice_line_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_invoice_line_invoice_id ON invoice_line (invoice_id)")

    # -- invoice_tax table ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE invoice_tax (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            invoice_id              UUID            NOT NULL,
            vat_rate_id             UUID            NOT NULL,

            vat_rate_label          TEXT            NOT NULL,
            vat_rate_percent        NUMERIC(6,3)    NOT NULL,
            effective_vat_percent   NUMERIC(6,3)    NOT NULL,
            taxable_amount          NUMERIC(18,3)   NOT NULL,
            tax_amount              NUMERIC(18,3)   NOT NULL,

            PRIMARY KEY (id),
            CONSTRAINT fk_invoice_tax_invoice
                FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE,
            CONSTRAINT fk_invoice_tax_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_invoice_tax_invoice_id ON invoice_tax (invoice_id)")

    # -- invoice_line_tax table -----------------------------------------------
    op.execute(
        """
        CREATE TABLE invoice_line_tax (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            invoice_line_id         UUID            NOT NULL,
            vat_rate_id             UUID            NOT NULL,

            vat_rate_label          TEXT            NOT NULL,
            vat_rate_percent        NUMERIC(6,3)    NOT NULL,
            effective_vat_percent   NUMERIC(6,3)    NOT NULL,
            taxable_amount          NUMERIC(18,3)   NOT NULL,
            tax_amount              NUMERIC(18,3)   NOT NULL,

            PRIMARY KEY (id),
            CONSTRAINT fk_invoice_line_tax_invoice_line
                FOREIGN KEY (invoice_line_id) REFERENCES invoice_line(id) ON DELETE CASCADE,
            CONSTRAINT fk_invoice_line_tax_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_invoice_line_tax_line_id ON invoice_line_tax (invoice_line_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invoice_line_tax")
    op.execute("DROP TABLE IF EXISTS invoice_tax")
    op.execute("DROP TABLE IF EXISTS invoice_line")
    op.execute("DROP TABLE IF EXISTS invoice")
    op.execute("DROP TYPE IF EXISTS discounttype")
    op.execute("DROP TYPE IF EXISTS invoicetaxmode")
    op.execute("DROP TYPE IF EXISTS invoicepaidstatus")
    op.execute("DROP TYPE IF EXISTS invoicestatus")
