"""content tables + invoice/quote additive content columns (M6 step 4)

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-11

Adds:
- PG enum types contentblockkind, documenttemplatescope
- ``document_template`` table (named library of reusable line-item bundles)
- ``document_template_line`` table (lines within a template, cascade on delete)
- ``content_block`` table (standard text blocks; partial unique on is_default)
- ``note_template`` table (reusable note snippets)
- Additive columns on ``invoice``: warranty_text, terms_text, bank_text,
  payment_terms_text
- Additive columns on ``quote``: warranty_text, terms_text, bank_text,
  payment_terms_text
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Enum types -----------------------------------------------------------
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE contentblockkind AS ENUM (
                'WARRANTY', 'TERMS', 'BANK', 'PAYMENT_TERMS'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE documenttemplatescope AS ENUM (
                'QUOTE', 'INVOICE', 'BOTH'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    # -- document_template table ----------------------------------------------
    op.execute(
        """
        CREATE TABLE document_template (
            id              UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id      UUID            NOT NULL,
            name            TEXT            NOT NULL,
            applies_to      documenttemplatescope NOT NULL,

            created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

            PRIMARY KEY (id),
            CONSTRAINT fk_document_template_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_document_template_company_name
                UNIQUE (company_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_template_company_id ON document_template (company_id)"
    )

    # -- document_template_line table -----------------------------------------
    op.execute(
        """
        CREATE TABLE document_template_line (
            id              UUID            NOT NULL DEFAULT gen_random_uuid(),
            template_id     UUID            NOT NULL,
            sort_order      INTEGER         NOT NULL DEFAULT 0,

            name            TEXT            NOT NULL,
            description     TEXT,
            quantity        NUMERIC(18,3)   NOT NULL,
            unit_id         UUID,
            unit_name       TEXT,
            unit_price      NUMERIC(18,3),
            discount_type   discounttype    NOT NULL DEFAULT 'NONE',
            discount_value  NUMERIC(10,3)   NOT NULL DEFAULT 0,
            vat_rate_id     UUID,

            PRIMARY KEY (id),
            CONSTRAINT fk_document_template_line_template
                FOREIGN KEY (template_id) REFERENCES document_template(id) ON DELETE CASCADE,
            CONSTRAINT fk_document_template_line_unit
                FOREIGN KEY (unit_id) REFERENCES unit(id) ON DELETE SET NULL,
            CONSTRAINT fk_document_template_line_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_template_line_template_id ON document_template_line (template_id)"
    )

    # -- content_block table --------------------------------------------------
    op.execute(
        """
        CREATE TABLE content_block (
            id              UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id      UUID            NOT NULL,
            kind            contentblockkind NOT NULL,
            name            TEXT            NOT NULL,
            body            TEXT            NOT NULL,
            is_default      BOOLEAN         NOT NULL DEFAULT false,

            created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

            PRIMARY KEY (id),
            CONSTRAINT fk_content_block_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_content_block_company_kind_name
                UNIQUE (company_id, kind, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_content_block_company_id ON content_block (company_id)"
    )
    # Partial unique: at most one default per (company_id, kind)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_content_block_company_kind_default
            ON content_block (company_id, kind)
            WHERE is_default
        """
    )

    # -- note_template table --------------------------------------------------
    op.execute(
        """
        CREATE TABLE note_template (
            id              UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id      UUID            NOT NULL,
            name            TEXT            NOT NULL,
            body            TEXT            NOT NULL,

            created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

            PRIMARY KEY (id),
            CONSTRAINT fk_note_template_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_note_template_company_name
                UNIQUE (company_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_note_template_company_id ON note_template (company_id)"
    )

    # -- Additive columns on invoice ------------------------------------------
    op.execute(
        "ALTER TABLE invoice ADD COLUMN warranty_text TEXT"
    )
    op.execute(
        "ALTER TABLE invoice ADD COLUMN terms_text TEXT"
    )
    op.execute(
        "ALTER TABLE invoice ADD COLUMN bank_text TEXT"
    )
    op.execute(
        "ALTER TABLE invoice ADD COLUMN payment_terms_text TEXT"
    )

    # -- Additive columns on quote --------------------------------------------
    op.execute(
        "ALTER TABLE quote ADD COLUMN warranty_text TEXT"
    )
    op.execute(
        "ALTER TABLE quote ADD COLUMN terms_text TEXT"
    )
    op.execute(
        "ALTER TABLE quote ADD COLUMN bank_text TEXT"
    )
    op.execute(
        "ALTER TABLE quote ADD COLUMN payment_terms_text TEXT"
    )


def downgrade() -> None:
    # -- Remove additive columns from quote -----------------------------------
    op.execute("ALTER TABLE quote DROP COLUMN IF EXISTS payment_terms_text")
    op.execute("ALTER TABLE quote DROP COLUMN IF EXISTS bank_text")
    op.execute("ALTER TABLE quote DROP COLUMN IF EXISTS terms_text")
    op.execute("ALTER TABLE quote DROP COLUMN IF EXISTS warranty_text")

    # -- Remove additive columns from invoice ---------------------------------
    op.execute("ALTER TABLE invoice DROP COLUMN IF EXISTS payment_terms_text")
    op.execute("ALTER TABLE invoice DROP COLUMN IF EXISTS bank_text")
    op.execute("ALTER TABLE invoice DROP COLUMN IF EXISTS terms_text")
    op.execute("ALTER TABLE invoice DROP COLUMN IF EXISTS warranty_text")

    # -- Drop tables (reverse dependency order) --------------------------------
    op.execute("DROP TABLE IF EXISTS document_template_line")
    op.execute("DROP TABLE IF EXISTS document_template")
    op.execute("DROP TABLE IF EXISTS content_block")
    op.execute("DROP TABLE IF EXISTS note_template")

    # -- Drop enum types ------------------------------------------------------
    op.execute("DROP TYPE IF EXISTS documenttemplatescope")
    op.execute("DROP TYPE IF EXISTS contentblockkind")
