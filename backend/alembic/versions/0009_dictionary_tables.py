"""payment_method, expense_category, and unit tables + seeds (M4 step 2)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-09

Creates three dictionary tables (all company-scoped):
- ``payment_method``: UNIQUE(company_id, name); seeds: Bank transfer / Cash.
- ``expense_category``: UNIQUE(company_id, name);
  seeds: Tool / Consumable / Product / Regular / Travel.
- ``unit``: UNIQUE(company_id, code); seeds: pcs=Piece / hr=Hour / m=Meter.

Seeds are inserted for every existing company row.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # payment_method ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE payment_method (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            company_id  UUID        NOT NULL,
            name        TEXT        NOT NULL,
            active      BOOLEAN     NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_payment_method_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_payment_method_company_name
                UNIQUE (company_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_payment_method_company_id ON payment_method (company_id)"
    )

    # expense_category -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE expense_category (
            id                  UUID        NOT NULL DEFAULT gen_random_uuid(),
            company_id          UUID        NOT NULL,
            name                TEXT        NOT NULL,
            description         TEXT,
            default_deductible  BOOLEAN,
            active              BOOLEAN     NOT NULL DEFAULT true,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_expense_category_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_expense_category_company_name
                UNIQUE (company_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_expense_category_company_id ON expense_category (company_id)"
    )

    # unit -------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE unit (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            company_id  UUID        NOT NULL,
            code        TEXT        NOT NULL,
            name        TEXT        NOT NULL,
            active      BOOLEAN     NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_unit_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_unit_company_code
                UNIQUE (company_id, code)
        )
        """
    )
    op.execute("CREATE INDEX ix_unit_company_id ON unit (company_id)")

    # Seeds for existing companies -------------------------------------------
    op.execute(
        """
        INSERT INTO payment_method (company_id, name)
        SELECT c.id, v.name
        FROM company c
        CROSS JOIN (VALUES ('Bank transfer'), ('Cash')) AS v(name)
        ON CONFLICT (company_id, name) DO NOTHING
        """
    )

    op.execute(
        """
        INSERT INTO expense_category (company_id, name, default_deductible)
        SELECT c.id, v.name, v.default_deductible
        FROM company c
        CROSS JOIN (
            VALUES
                ('Tool',        true),
                ('Consumable',  true),
                ('Product',     true),
                ('Regular',     true),
                ('Travel',      NULL)
        ) AS v(name, default_deductible)
        ON CONFLICT (company_id, name) DO NOTHING
        """
    )

    op.execute(
        """
        INSERT INTO unit (company_id, code, name)
        SELECT c.id, v.code, v.name
        FROM company c
        CROSS JOIN (
            VALUES
                ('pcs', 'Piece'),
                ('hr',  'Hour'),
                ('m',   'Meter')
        ) AS v(code, name)
        ON CONFLICT (company_id, code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS unit")
    op.execute("DROP TABLE IF EXISTS expense_category")
    op.execute("DROP TABLE IF EXISTS payment_method")
