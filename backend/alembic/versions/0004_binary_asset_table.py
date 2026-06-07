"""binary_asset table + company.logo_id FK

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-07

Creates the ``binary_asset`` table for small-blob storage (logos, icons)
and adds the FK from ``company.logo_id`` to ``binary_asset.id`` with
``ON DELETE SET NULL`` (red-line 3 / M2 step 2).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Create binary_asset table ----------------------------------------------
    op.create_table(
        "binary_asset",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
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

    # -- Add FK from company.logo_id to binary_asset.id -------------------------
    op.create_foreign_key(
        "fk_company_logo_id",
        source_table="company",
        referent_table="binary_asset",
        local_cols=["logo_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_company_logo_id", "company", type_="foreignkey")
    op.drop_table("binary_asset")
