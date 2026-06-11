"""quote / quote_line / quote_tax / quote_line_tax tables (M6 step 2)

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-11

Adds:
- PG enum type quotestatus
- ``quote`` table (mirrors invoice; no paid_status / due_amount;
  adds valid_until, converted_invoice_id)
- ``quote_line`` table with cascade-delete FK to quote
- ``quote_tax`` table with cascade-delete FK to quote
- ``quote_line_tax`` table with cascade-delete FK to quote_line
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Enum types -----------------------------------------------------------
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE quotestatus AS ENUM ('DRAFT', 'SENT', 'ACCEPTED', 'REJECTED', 'EXPIRED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    # -- quote table ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE quote (
            id                          UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id                  UUID            NOT NULL,
            customer_id                 UUID            NOT NULL,

            quote_number                TEXT            NOT NULL,
            sequence_number             BIGINT          NOT NULL,
            customer_sequence_number    BIGINT,
            unique_hash                 TEXT,
            reference_number            TEXT,

            quote_date                  DATE            NOT NULL,
            valid_until                 DATE,

            status                      quotestatus     NOT NULL DEFAULT 'DRAFT',
            converted_invoice_id        UUID,

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

            base_subtotal_excl_vat      NUMERIC(18,3)   NOT NULL,
            base_line_discount_total    NUMERIC(18,3)   NOT NULL,
            base_taxable_amount         NUMERIC(18,3)   NOT NULL,
            base_vat_total              NUMERIC(18,3)   NOT NULL,
            base_total_incl_vat         NUMERIC(18,3)   NOT NULL,

            notes                       TEXT,
            creator_id                  UUID,

            created_at                  TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT now(),

            PRIMARY KEY (id),
            CONSTRAINT fk_quote_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_quote_customer
                FOREIGN KEY (customer_id) REFERENCES customer(id) ON DELETE RESTRICT,
            CONSTRAINT fk_quote_vat_treatment
                FOREIGN KEY (vat_treatment_id) REFERENCES vat_treatment(id) ON DELETE RESTRICT,
            CONSTRAINT fk_quote_document_vat_rate
                FOREIGN KEY (document_vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT,
            CONSTRAINT fk_quote_converted_invoice
                FOREIGN KEY (converted_invoice_id) REFERENCES invoice(id) ON DELETE SET NULL,
            CONSTRAINT fk_quote_creator
                FOREIGN KEY (creator_id) REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT uq_quote_company_number
                UNIQUE (company_id, quote_number)
        )
        """
    )
    op.execute("CREATE INDEX ix_quote_company_id ON quote (company_id)")
    op.execute("CREATE INDEX ix_quote_customer_id ON quote (customer_id)")
    op.execute("CREATE INDEX ix_quote_quote_date ON quote (quote_date)")

    # -- quote_line table -----------------------------------------------------
    op.execute(
        """
        CREATE TABLE quote_line (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            quote_id                UUID            NOT NULL,
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
            CONSTRAINT fk_quote_line_quote
                FOREIGN KEY (quote_id) REFERENCES quote(id) ON DELETE CASCADE,
            CONSTRAINT fk_quote_line_product
                FOREIGN KEY (product_id) REFERENCES product(id) ON DELETE SET NULL,
            CONSTRAINT fk_quote_line_unit
                FOREIGN KEY (unit_id) REFERENCES unit(id) ON DELETE SET NULL,
            CONSTRAINT fk_quote_line_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_quote_line_quote_id ON quote_line (quote_id)")

    # -- quote_tax table ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE quote_tax (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            quote_id                UUID            NOT NULL,
            vat_rate_id             UUID            NOT NULL,

            vat_rate_label          TEXT            NOT NULL,
            vat_rate_percent        NUMERIC(6,3)    NOT NULL,
            effective_vat_percent   NUMERIC(6,3)    NOT NULL,
            taxable_amount          NUMERIC(18,3)   NOT NULL,
            tax_amount              NUMERIC(18,3)   NOT NULL,

            PRIMARY KEY (id),
            CONSTRAINT fk_quote_tax_quote
                FOREIGN KEY (quote_id) REFERENCES quote(id) ON DELETE CASCADE,
            CONSTRAINT fk_quote_tax_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_quote_tax_quote_id ON quote_tax (quote_id)")

    # -- quote_line_tax table -------------------------------------------------
    op.execute(
        """
        CREATE TABLE quote_line_tax (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            quote_line_id           UUID            NOT NULL,
            vat_rate_id             UUID            NOT NULL,

            vat_rate_label          TEXT            NOT NULL,
            vat_rate_percent        NUMERIC(6,3)    NOT NULL,
            effective_vat_percent   NUMERIC(6,3)    NOT NULL,
            taxable_amount          NUMERIC(18,3)   NOT NULL,
            tax_amount              NUMERIC(18,3)   NOT NULL,

            PRIMARY KEY (id),
            CONSTRAINT fk_quote_line_tax_quote_line
                FOREIGN KEY (quote_line_id) REFERENCES quote_line(id) ON DELETE CASCADE,
            CONSTRAINT fk_quote_line_tax_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_quote_line_tax_line_id ON quote_line_tax (quote_line_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quote_line_tax")
    op.execute("DROP TABLE IF EXISTS quote_tax")
    op.execute("DROP TABLE IF EXISTS quote_line")
    op.execute("DROP TABLE IF EXISTS quote")
    op.execute("DROP TYPE IF EXISTS quotestatus")
