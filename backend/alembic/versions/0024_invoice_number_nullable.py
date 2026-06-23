"""invoice number/sequence nullable – defer numbering to issue time (fix)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-23

Defers legal invoice numbering from create time to the DRAFT -> SENT (issue)
transition (see ``docs/plan/fixes/invoice-number-on-issue.md``). A DRAFT no
longer consumes a number, so deleting a draft leaves no gap in the sequence.

This migration only relaxes two NOT NULL constraints:
- ``invoice.invoice_number``  TEXT   -> nullable
- ``invoice.sequence_number`` BIGINT -> nullable

``customer_sequence_number`` is already nullable. The unique constraint
``uq_invoice_company_number (company_id, invoice_number)`` is UNCHANGED:
Postgres treats NULLs as distinct, so multiple unnumbered drafts coexist while
uniqueness is still enforced once a number is assigned.

This is a purely non-destructive, additive-in-spirit change: existing rows keep
their values; only newly created drafts will carry NULL number/sequence.

.. warning::
   ``downgrade`` re-adds ``NOT NULL`` on both columns and will FAIL if any
   unnumbered drafts (NULL ``invoice_number`` / ``sequence_number`` rows) exist
   at downgrade time. This is intentional for a forward-only fix: before
   downgrading you must first delete or back-fill any unnumbered drafts.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE invoice
            ALTER COLUMN invoice_number DROP NOT NULL,
            ALTER COLUMN sequence_number DROP NOT NULL
        """
    )


def downgrade() -> None:
    # WARNING: this fails if any unnumbered drafts (NULL number/sequence) exist.
    # Delete or back-fill those rows before downgrading.
    op.execute(
        """
        ALTER TABLE invoice
            ALTER COLUMN invoice_number SET NOT NULL,
            ALTER COLUMN sequence_number SET NOT NULL
        """
    )
