"""estimate / estimate_group / estimate_line tables (M6.5 step 2)

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-11

Adds:
- ``estimate`` table (root work sheet; company-scoped; no enumerated status)
- ``estimate_group`` table (customer-facing projection; cascade on delete from estimate)
- ``estimate_line`` table (internal cost row; cascade on delete from estimate)
- Indexes on company_id / estimate_id / group_id foreign keys

No new enum types – estimate uses plain text fields.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- estimate table -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE estimate (
            id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id          UUID            NOT NULL,
            name                TEXT            NOT NULL,
            customer_id         UUID,
            notes               TEXT,
            generated_quote_id  UUID,
            total_margin        NUMERIC(18,3)   NOT NULL DEFAULT 0,
            total_excl_vat      NUMERIC(18,3)   NOT NULL DEFAULT 0,
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT pk_estimate PRIMARY KEY (id),
            CONSTRAINT fk_estimate_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_estimate_customer
                FOREIGN KEY (customer_id) REFERENCES customer(id) ON DELETE SET NULL,
            CONSTRAINT fk_estimate_generated_quote
                FOREIGN KEY (generated_quote_id) REFERENCES quote(id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_estimate_company_id  ON estimate (company_id)")
    op.execute("CREATE INDEX ix_estimate_customer_id ON estimate (customer_id)")

    # -- estimate_group table -------------------------------------------------
    op.execute(
        """
        CREATE TABLE estimate_group (
            id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
            estimate_id         UUID            NOT NULL,
            sort_order          INT             NOT NULL DEFAULT 0,
            public_description  TEXT            NOT NULL,
            vat_rate_id         UUID,
            group_sell_excl_vat NUMERIC(18,3)   NOT NULL DEFAULT 0,
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT pk_estimate_group PRIMARY KEY (id),
            CONSTRAINT fk_estimate_group_estimate
                FOREIGN KEY (estimate_id) REFERENCES estimate(id) ON DELETE CASCADE,
            CONSTRAINT fk_estimate_group_vat_rate
                FOREIGN KEY (vat_rate_id) REFERENCES vat_rate(id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_estimate_group_estimate_id ON estimate_group (estimate_id)"
    )

    # -- estimate_line table --------------------------------------------------
    op.execute(
        """
        CREATE TABLE estimate_line (
            id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
            estimate_id         UUID            NOT NULL,
            group_id            UUID,
            sort_order          INT             NOT NULL DEFAULT 0,
            product_id          UUID,
            name                TEXT            NOT NULL,
            description         TEXT,
            unit_id             UUID,
            unit_name           TEXT,
            unit_cost_excl_vat  NUMERIC(14,3)   NOT NULL,
            quantity            NUMERIC(18,3)   NOT NULL,
            margin_rate         NUMERIC(6,4)    NOT NULL DEFAULT 0,
            line_total          NUMERIC(18,3)   NOT NULL,
            margin_amount       NUMERIC(18,3)   NOT NULL,
            line_sell_excl_vat  NUMERIC(18,3)   NOT NULL,
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT pk_estimate_line PRIMARY KEY (id),
            CONSTRAINT fk_estimate_line_estimate
                FOREIGN KEY (estimate_id) REFERENCES estimate(id) ON DELETE CASCADE,
            CONSTRAINT fk_estimate_line_group
                FOREIGN KEY (group_id) REFERENCES estimate_group(id) ON DELETE SET NULL,
            CONSTRAINT fk_estimate_line_product
                FOREIGN KEY (product_id) REFERENCES product(id) ON DELETE SET NULL,
            CONSTRAINT fk_estimate_line_unit
                FOREIGN KEY (unit_id) REFERENCES unit(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_estimate_line_estimate_id ON estimate_line (estimate_id)"
    )
    op.execute(
        "CREATE INDEX ix_estimate_line_group_id ON estimate_line (group_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS estimate_line")
    op.execute("DROP TABLE IF EXISTS estimate_group")
    op.execute("DROP TABLE IF EXISTS estimate")
