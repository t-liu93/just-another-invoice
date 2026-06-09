"""vat_rate and vat_treatment tables + NL seeds (M4 step 1)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-09

Creates two VAT dictionary tables:
- ``vat_rate``: per-item tax rate (21/9/0 NL seeds).
- ``vat_treatment``: document-level processing mode (8 system seeds: 4 sales + 4 purchase).

Both tables are company-scoped (UNIQUE per company).  ``report_box`` is NULL
in M4; the author and agent will co-define the BTW box mapping in M10.
Seeds are inserted for every existing company.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create PostgreSQL enums (idempotent DO blocks).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE vattreatmentside AS ENUM ('SALES', 'PURCHASE');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE vattreatmenteffect AS ENUM (
                'APPLY_RATE', 'ZERO_REVERSE', 'ZERO_EXPORT', 'EXEMPT'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    # Create vat_rate table.
    op.execute(
        """
        CREATE TABLE vat_rate (
            id          UUID         NOT NULL DEFAULT gen_random_uuid(),
            company_id  UUID         NOT NULL,
            label       TEXT         NOT NULL,
            percent     NUMERIC(6,3) NOT NULL,
            active      BOOLEAN      NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_vat_rate_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_vat_rate_company_label
                UNIQUE (company_id, label)
        )
        """
    )
    op.execute("CREATE INDEX ix_vat_rate_company_id ON vat_rate (company_id)")

    # Create vat_treatment table.
    op.execute(
        """
        CREATE TABLE vat_treatment (
            id           UUID                NOT NULL DEFAULT gen_random_uuid(),
            company_id   UUID                NOT NULL,
            code         TEXT                NOT NULL,
            label        TEXT                NOT NULL,
            side         vattreatmentside    NOT NULL,
            effect       vattreatmenteffect  NOT NULL,
            report_box   TEXT,
            requires_icp BOOLEAN             NOT NULL DEFAULT false,
            deductible   BOOLEAN,
            active       BOOLEAN             NOT NULL DEFAULT true,
            created_at   TIMESTAMPTZ         NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ         NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_vat_treatment_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_vat_treatment_company_code
                UNIQUE (company_id, code)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_vat_treatment_company_id ON vat_treatment (company_id)"
    )

    # Seed default rates and treatments for every existing company.
    op.execute(
        """
        INSERT INTO vat_rate (company_id, label, percent)
        SELECT c.id, v.label, v.percent
        FROM company c
        CROSS JOIN (
            VALUES
                ('NL standard (21%)', 21.000),
                ('NL reduced (9%)',    9.000),
                ('Zero (0%)',          0.000)
        ) AS v(label, percent)
        ON CONFLICT (company_id, label) DO NOTHING
        """
    )

    op.execute(
        """
        INSERT INTO vat_treatment
            (company_id, code, label, side, effect, requires_icp, deductible)
        SELECT c.id, v.code, v.label,
               v.side::vattreatmentside, v.effect::vattreatmenteffect,
               v.requires_icp, v.deductible
        FROM company c
        CROSS JOIN (
            VALUES
                -- Sales side (4)
                ('NL_DOMESTIC',          'NL Domestic',
                 'SALES', 'APPLY_RATE',  false, NULL),
                ('EU_B2B_REVERSE',       'EU B2B (Reverse Charge)',
                 'SALES', 'ZERO_REVERSE', true, NULL),
                ('EU_B2C',               'EU B2C',
                 'SALES', 'APPLY_RATE',  false, NULL),
                ('EXPORT_NON_EU',        'Export (Non-EU)',
                 'SALES', 'ZERO_EXPORT', false, NULL),
                -- Purchase side (4)
                ('NL_DOMESTIC_PURCH',    'NL Domestic (Purchase)',
                 'PURCHASE', 'APPLY_RATE',  false, true),
                ('EU_B2B_REVERSE_PURCH', 'EU B2B Reverse Charge (Purchase)',
                 'PURCHASE', 'ZERO_REVERSE', true, true),
                ('EU_B2C_PURCH',         'EU B2C (Purchase)',
                 'PURCHASE', 'APPLY_RATE',  false, NULL),
                ('IMPORT_NON_EU',        'Import (Non-EU)',
                 'PURCHASE', 'APPLY_RATE',  false, NULL)
        ) AS v(code, label, side, effect, requires_icp, deductible)
        ON CONFLICT (company_id, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vat_treatment")
    op.execute("DROP TABLE IF EXISTS vat_rate")
    op.execute("DROP TYPE IF EXISTS vattreatmenteffect")
    op.execute("DROP TYPE IF EXISTS vattreatmentside")
