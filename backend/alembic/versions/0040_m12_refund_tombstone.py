"""Retain Refund Confirmation owners when a mutable Refund is deleted.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A Refund remains a Payment owner for immutable artifacts and successful
    # EmailLog attachments.  Operational projections filter this marker.
    op.execute("ALTER TABLE payment ADD COLUMN deleted_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX ix_payment_active_refund ON payment (company_id, credit_note_id) "
        "WHERE direction = 'REFUND' AND deleted_at IS NULL"
    )


def downgrade() -> None:
    # Dropping this marker would make retained deleted Refund rows appear live
    # again in 0039.  Never delete those rows here: they own immutable PDF
    # artifacts and successful EmailLog attachments.
    # SHARE ROW EXCLUSIVE conflicts with application UPDATE/DELETE's ROW
    # EXCLUSIVE lock while allowing readers.  It is held until the migration
    # transaction completes, closing the check-to-DDL tombstone race.
    op.execute("LOCK TABLE payment IN SHARE ROW EXCLUSIVE MODE")
    tombstone = op.get_bind().execute(
        text("SELECT 1 FROM payment WHERE deleted_at IS NOT NULL LIMIT 1")
    ).scalar_one_or_none()
    if tombstone is not None:
        raise RuntimeError(
            "Cannot downgrade 0040 while deleted Refund tombstones exist. "
            "Restore or otherwise handle deleted refunds before downgrading."
        )
    op.execute("DROP INDEX ix_payment_active_refund")
    op.execute("ALTER TABLE payment DROP COLUMN deleted_at")
