"""Harden M12 snapshot RLS and grant the runtime application role.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import dialect

from alembic import op
from jai.config import get_settings

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role_identifier() -> str:
    role = get_settings().postgres_app_user
    if not role:
        raise ValueError("POSTGRES_APP_USER must be a non-empty PostgreSQL role identifier.")

    # This migration accepts the same PostgreSQL role names as the provisioner
    # (which uses psql's %I / :"app_user" quoting).  Always use the dialect's
    # identifier preparer before interpolating the resulting SQL identifier:
    # role names such as ``runtime-role`` need quoting, and embedded quotes
    # must never be able to escape a GRANT statement.
    return dialect().identifier_preparer.quote_identifier(role)


def upgrade() -> None:
    # SET LOCAL restores an already-known custom GUC to an empty string after
    # COMMIT/ROLLBACK. ``missing_ok`` only handles an unknown setting, hence
    # NULLIF is required for fresh and pool-reused transactions to deny rows.
    expression = "company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid"
    for table, policy in (
        ("invoice_party_snapshot", "invoice_party_snapshot_company_isolation"),
        ("invoice_credit_basis_line", "invoice_credit_basis_line_company_isolation"),
    ):
        op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING ({expression}) "
            f"WITH CHECK ({expression})"
        )

    app_role = _role_identifier()
    op.execute(f"GRANT USAGE ON SCHEMA public TO {app_role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_role}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {app_role}"
    )


def downgrade() -> None:
    # Do not revoke live application permissions during a schema downgrade.
    expression = "company_id = current_setting('jai.company_id', true)::uuid"
    for table, policy in (
        ("invoice_party_snapshot", "invoice_party_snapshot_company_isolation"),
        ("invoice_credit_basis_line", "invoice_credit_basis_line_company_isolation"),
    ):
        op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING ({expression}) "
            f"WITH CHECK ({expression})"
        )
