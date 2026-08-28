"""Revoke legacy runtime sequence UPDATE grants.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import dialect

from alembic import op
from jai.config import get_settings

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role_identifier() -> str:
    role = get_settings().postgres_app_user
    if not role:
        raise ValueError("POSTGRES_APP_USER must be a non-empty PostgreSQL role identifier.")
    return dialect().identifier_preparer.quote_identifier(role)


def upgrade() -> None:
    # Older provisioners granted UPDATE, which enables setval().  GRANT is
    # additive, so 0030's smaller GRANT could not remove that live privilege.
    # Revoke both current and migration-owner default ACLs to converge already
    # provisioned databases and all future sequence creation.
    app_role = _role_identifier()
    op.execute(f"REVOKE UPDATE ON ALL SEQUENCES IN SCHEMA public FROM {app_role}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE UPDATE ON SEQUENCES FROM {app_role}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {app_role}"
    )


def downgrade() -> None:
    # A schema downgrade must not reintroduce a runtime privilege that enables
    # counter rewrites.  0030 remains sufficient for the preceding schema.
    pass
