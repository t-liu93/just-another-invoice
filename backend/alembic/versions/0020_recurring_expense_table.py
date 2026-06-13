"""recurring_expense table + expense.recurring_expense_id FK (M8 step 3)

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-13

Adds:
- ``recurring_expense`` table
  - company_id FK RESTRICT (red-line 2)
  - name TEXT NOT NULL
  - Template fields mirroring ``expense``:
      category_id FK SET NULL + category_name snapshot (D12)
      supplier_name TEXT nullable
      vat_treatment_id FK RESTRICT + code/label/effect snapshots (D7)
      vat_rate_id FK RESTRICT + percent/label snapshots (D7)
      net_amount / vat_amount / gross_amount NUMERIC(18,3) (D11)
      deductible BOOLEAN
      note TEXT
  - Frequency: TEXT NOT NULL (MONTHLY | QUARTERLY | YEARLY)
  - Schedule: start_date DATE NOT NULL, end_date DATE nullable,
              max_occurrences INTEGER nullable
  - Tracking: occurrences_generated INTEGER DEFAULT 0,
              next_run_date DATE NOT NULL,
              last_generated_at TIMESTAMPTZ nullable
  - active BOOLEAN NOT NULL DEFAULT true
  - creator_id FK SET NULL
  - Timestamps
- Indexes: ix_recurring_expense_company_id, ix_recurring_expense_next_run_date
- Additive column ``expense.recurring_expense_id`` UUID nullable FK SET NULL
  + index ix_expense_recurring_expense_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- 1. Create recurring_expense table ------------------------------------
    op.execute(
        """
        CREATE TABLE recurring_expense (
            id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id              UUID            NOT NULL,

            -- Human-readable name (red-line 10)
            name                    TEXT            NOT NULL,

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

            -- Template amounts (D8 / D11)
            net_amount              NUMERIC(18, 3)  NOT NULL,
            vat_amount              NUMERIC(18, 3)  NOT NULL,
            gross_amount            NUMERIC(18, 3)  NOT NULL,

            -- Deductibility (D9)
            deductible              BOOLEAN         NOT NULL,

            -- Notes (red-line 10)
            note                    TEXT,

            -- Schedule
            frequency               TEXT            NOT NULL,
            start_date              DATE            NOT NULL,
            end_date                DATE,
            max_occurrences         INTEGER,

            -- Generation tracking
            occurrences_generated   INTEGER         NOT NULL DEFAULT 0,
            next_run_date           DATE            NOT NULL,
            last_generated_at       TIMESTAMPTZ,

            -- Active flag
            active                  BOOLEAN         NOT NULL DEFAULT true,

            -- Creator (SET NULL: preserve template if user is deleted)
            creator_id              UUID,

            -- Timestamps
            created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT pk_recurring_expense PRIMARY KEY (id),
            CONSTRAINT fk_recurring_expense_company
                FOREIGN KEY (company_id)
                REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_recurring_expense_category
                FOREIGN KEY (category_id)
                REFERENCES expense_category(id) ON DELETE SET NULL,
            CONSTRAINT fk_recurring_expense_vat_treatment
                FOREIGN KEY (vat_treatment_id)
                REFERENCES vat_treatment(id) ON DELETE RESTRICT,
            CONSTRAINT fk_recurring_expense_vat_rate
                FOREIGN KEY (vat_rate_id)
                REFERENCES vat_rate(id) ON DELETE RESTRICT,
            CONSTRAINT fk_recurring_expense_creator
                FOREIGN KEY (creator_id)
                REFERENCES "user"(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_recurring_expense_company_id   ON recurring_expense (company_id)"
    )
    op.execute(
        "CREATE INDEX ix_recurring_expense_next_run_date ON recurring_expense (next_run_date)"
    )

    # -- 2. Additive column: expense.recurring_expense_id (FK SET NULL) -------
    # Step 1 deliberately did not add this column; it requires recurring_expense
    # to exist first (referential integrity).
    op.execute(
        """
        ALTER TABLE expense
            ADD COLUMN IF NOT EXISTS recurring_expense_id UUID,
            ADD CONSTRAINT fk_expense_recurring_expense
                FOREIGN KEY (recurring_expense_id)
                REFERENCES recurring_expense(id) ON DELETE SET NULL
        """
    )
    op.execute(
        "CREATE INDEX ix_expense_recurring_expense_id ON expense (recurring_expense_id)"
        " WHERE recurring_expense_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_expense_recurring_expense_id")
    op.execute(
        "ALTER TABLE expense DROP CONSTRAINT IF EXISTS fk_expense_recurring_expense"
    )
    op.execute("ALTER TABLE expense DROP COLUMN IF EXISTS recurring_expense_id")
    op.execute("DROP TABLE IF EXISTS recurring_expense")
