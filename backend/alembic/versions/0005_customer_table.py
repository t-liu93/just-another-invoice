"""customer table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-09

Creates the ``customer`` table – the first per-company business data table
(M3 step 1).  Establishes the ``company_id NOT NULL FK → company.id``
pattern (red-line 2 / §3.3).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.Text(), nullable=True),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("vat_id", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column(
            "extra",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_company_id", "customer", ["company_id"], unique=False
    )
    op.create_foreign_key(
        "fk_customer_company_id",
        source_table="customer",
        referent_table="company",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_customer_company_id", "customer", type_="foreignkey"
    )
    op.drop_index("ix_customer_company_id", table_name="customer")
    op.drop_table("customer")
