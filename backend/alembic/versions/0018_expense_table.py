"""expense table (M8 step 1)

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-13

Adds:
- ``expense`` table (company-scoped; category FK SET NULL; vat_treatment/vat_rate
  FK RESTRICT; creator FK SET NULL; all money columns NUMERIC(18,3))
- Indexes on company_id / expense_date / category_id

Does NOT add ``recurring_expense_id`` column – that will be added in step 3 as
an additive migration once the ``recurring_expense`` table exists.

Note on vat_treatment.effect: stored as TEXT snapshot (not the PostgreSQL enum)
because the ORM column on Expense is Text, not the VatTreatmentEffect enum.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE expense (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id              UUID            NOT NULL,
            expense_date            DATE            NOT NULL,

            -- Category (FK SET NULL + name snapshot, D12)
            category_id             UUID,
            category_name           TEXT,

            -- Supplier (free-text v1)
            supplier_name           TEXT,

            -- VAT treatment snapshot (FK RESTRICT, D7)
            vat_treatment_id        UUID,
            vat_treatment_code      TEXT            NOT NULL,
            vat_treatment_label     TEXT            NOT NULL,
            vat_treatment_effect    TEXT            NOT NULL,

            -- VAT rate snapshot (FK RESTRICT, D7)
            vat_rate_id             UUID,
            vat_rate_percent        NUMERIC(6, 3)   NOT NULL,
            vat_rate_label          TEXT            NOT NULL,

            -- Amounts (D8 / D11)
            net_amount              NUMERIC(18, 3)  NOT NULL,
            vat_amount              NUMERIC(18, 3)  NOT NULL,
            gross_amount            NUMERIC(18, 3)  NOT NULL,

            -- Deductibility (D9)
            deductible              BOOLEAN         NOT NULL,

            -- Currency (D10)
            currency                VARCHAR(3)      NOT NULL,
            exchange_rate           NUMERIC(18, 8)  NOT NULL DEFAULT 1,
            base_net_amount         NUMERIC(18, 3)  NOT NULL,
            base_vat_amount         NUMERIC(18, 3)  NOT NULL,
            base_gross_amount       NUMERIC(18, 3)  NOT NULL,

            -- Reference / notes (red-line 10)
            reference               TEXT,
            note                    TEXT,

            -- Draft flag (D3: step 3 recurring expenses use this)
            is_draft                BOOLEAN         NOT NULL DEFAULT false,

            -- Creator (SET NULL: preserve expense if user is deleted)
            creator_id              UUID,

            -- Timestamps
            created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT pk_expense PRIMARY KEY (id),
            CONSTRAINT fk_expense_company
                FOREIGN KEY (company_id)
                REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_expense_category
                FOREIGN KEY (category_id)
                REFERENCES expense_category(id) ON DELETE SET NULL,
            CONSTRAINT fk_expense_vat_treatment
                FOREIGN KEY (vat_treatment_id)
                REFERENCES vat_treatment(id) ON DELETE RESTRICT,
            CONSTRAINT fk_expense_vat_rate
                FOREIGN KEY (vat_rate_id)
                REFERENCES vat_rate(id) ON DELETE RESTRICT,
            CONSTRAINT fk_expense_creator
                FOREIGN KEY (creator_id)
                REFERENCES "user"(id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_expense_company_id   ON expense (company_id)")
    op.execute("CREATE INDEX ix_expense_expense_date ON expense (expense_date)")
    op.execute("CREATE INDEX ix_expense_category_id  ON expense (category_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS expense")
