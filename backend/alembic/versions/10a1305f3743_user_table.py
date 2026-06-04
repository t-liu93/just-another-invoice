"""user_table

Revision ID: 10a1305f3743
Revises: 0002
Create Date: 2026-06-04 10:46:12.548595
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "10a1305f3743"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=False,
        ),
        sa.Column(
            "hashed_password",
            sa.String(length=1024),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        # -- Reserved / MFA columns (M1) --
        sa.Column(
            "company_id",
            sa.UUID(),
            nullable=True,
            comment="FK to company table (added in M2).",
        ),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default="owner",
        ),
        sa.Column("totp_secret", sa.Text(), nullable=True),
        sa.Column(
            "mfa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        # -- Timestamps --
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
    op.create_index(op.f("ix_user_company_id"), "user", ["company_id"], unique=False)
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_index(op.f("ix_user_company_id"), table_name="user")
    op.drop_table("user")
