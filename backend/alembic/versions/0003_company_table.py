"""company_table + user.company_id FK

Revision ID: 0003
Revises: 10a1305f3743
Create Date: 2026-06-05

Creates the ``company`` table (singleton business profile) and adds a
foreign key from ``user.company_id`` to ``company.id`` with
``ON DELETE RESTRICT`` (red-line 2/3).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "10a1305f3743"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Create company table --------------------------------------------------
    op.create_table(
        "company",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("vat_id", sa.Text(), nullable=True),
        sa.Column("coc_number", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=True),
        sa.Column("address_line2", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column(
            "country_code",
            sa.String(length=2),
            nullable=True,
            comment="ISO 3166-1 alpha-2 country code.",
        ),
        sa.Column(
            "base_currency",
            sa.String(length=3),
            nullable=False,
            comment="ISO 4217 3-letter currency code.",
        ),
        sa.Column(
            "logo_id",
            sa.UUID(),
            nullable=True,
            comment="FK to binary_asset.id (ON DELETE SET NULL, added in step 2).",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # -- Add FK from user.company_id to company.id -----------------------------
    op.create_foreign_key(
        "fk_user_company_id",
        source_table="user",
        referent_table="company",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_user_company_id", "user", type_="foreignkey")
    op.drop_table("company")
