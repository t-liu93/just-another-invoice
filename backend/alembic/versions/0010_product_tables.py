"""product_category and product tables (M4 step 3)

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-10

Creates two tables for the internal cost catalogue:
- ``product_category``: UNIQUE(company_id, name); no seeds (JIT review: leave
  empty so each user fills in their own categories).
- ``product``: internal cost/margin/VAT entry; category_id / unit_id /
  default_vat_rate_id are all SET NULL on referenced-row deletion.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # product_category -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE product_category (
            id                  UUID        NOT NULL DEFAULT gen_random_uuid(),
            company_id          UUID        NOT NULL,
            name                TEXT        NOT NULL,
            default_margin_rate NUMERIC(6,4),
            active              BOOLEAN     NOT NULL DEFAULT true,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_product_category_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_product_category_company_name
                UNIQUE (company_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_product_category_company_id ON product_category (company_id)"
    )

    # product ----------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE product (
            id                      UUID        NOT NULL DEFAULT gen_random_uuid(),
            company_id              UUID        NOT NULL,
            name                    TEXT        NOT NULL,
            sku                     TEXT,
            category_id             UUID,
            unit_id                 UUID,
            purchase_cost_excl_vat  NUMERIC(14,3),
            margin_rate             NUMERIC(6,4),
            default_vat_rate_id     UUID,
            supplier                TEXT,
            extra                   JSONB       NOT NULL DEFAULT '{}',
            active                  BOOLEAN     NOT NULL DEFAULT true,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_product_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_product_category
                FOREIGN KEY (category_id) REFERENCES product_category(id) ON DELETE SET NULL,
            CONSTRAINT fk_product_unit
                FOREIGN KEY (unit_id) REFERENCES unit(id) ON DELETE SET NULL,
            CONSTRAINT fk_product_vat_rate
                FOREIGN KEY (default_vat_rate_id) REFERENCES vat_rate(id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_product_company_id ON product (company_id)")
    op.execute("CREATE INDEX ix_product_name ON product (company_id, name)")
    op.execute(
        "CREATE INDEX ix_product_sku ON product (company_id, sku) WHERE sku IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS product")
    op.execute("DROP TABLE IF EXISTS product_category")
