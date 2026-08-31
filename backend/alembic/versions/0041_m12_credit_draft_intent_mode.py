"""Persist the semantic full-remaining Credit DRAFT intent.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The pre-0041 representation persisted materialised correction rows for
    # both "full remaining" and an explicit selection of every row.  Those
    # states are deliberately indistinguishable, so backfilling either boolean
    # would silently alter intent on a later issue.  Preserve that uncertainty
    # and require the user to choose a mode before recalculating/saving.
    op.execute("ALTER TABLE invoice_correction ADD COLUMN full_remaining BOOLEAN")
    op.execute(
        "ALTER TABLE invoice_correction ADD COLUMN intent_provenance VARCHAR(32) "
        "NOT NULL DEFAULT 'MIGRATED_AMBIGUOUS'"
    )
    # New writes always set both values explicitly in the service.  The server
    # default is only a safety net for non-ORM writers after this migration.
    op.execute(
        "ALTER TABLE invoice_correction ALTER COLUMN intent_provenance "
        "SET DEFAULT 'NATIVE'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE invoice_correction DROP COLUMN intent_provenance")
    op.execute("ALTER TABLE invoice_correction DROP COLUMN full_remaining")
