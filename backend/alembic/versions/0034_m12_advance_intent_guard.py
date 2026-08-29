"""Persist Formal Advance intent and enforce one open Advance draft per Quote.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Never infer intent from rounded draft lines. Existing legacy Advance
    # drafts remain representable with NULL provenance and are rejected at
    # issue until an operator deliberately edits them through the command API.
    op.execute("ALTER TABLE invoice ADD COLUMN advance_input_mode TEXT")
    op.execute("ALTER TABLE invoice ADD COLUMN advance_gross_amount NUMERIC(18,3)")
    op.execute("ALTER TABLE invoice ADD COLUMN advance_percentage NUMERIC(6,3)")
    op.execute(
        "ALTER TABLE invoice ADD CONSTRAINT ck_invoice_advance_intent_shape CHECK ("
        "(advance_input_mode IS NULL AND advance_gross_amount IS NULL "
        "AND advance_percentage IS NULL) OR "
        "(advance_input_mode = 'GROSS_AMOUNT' AND advance_gross_amount IS NOT NULL "
        "AND advance_gross_amount > 0 AND advance_percentage IS NULL) OR "
        "(advance_input_mode = 'PERCENTAGE' AND advance_percentage IS NOT NULL "
        "AND advance_percentage > 0 AND advance_percentage <= 100 AND advance_gross_amount IS NULL)"
        ")"
    )
    # Do not silently cancel/delete one old draft merely to make the new
    # invariant true. Abort before the index exists, leaving all data intact
    # for an explicit operator decision without historical amount drift.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT quote_id FROM invoice "
        "WHERE document_kind = 'ADVANCE' AND status = 'DRAFT' AND quote_id IS NOT NULL "
        "GROUP BY quote_id HAVING count(*) > 1) THEN "
        "RAISE EXCEPTION 'Cannot add Advance DRAFT uniqueness: duplicate legacy drafts "
        "require manual resolution'; "
        "END IF; END $$"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_invoice_advance_quote_draft "
        "ON invoice (quote_id) WHERE document_kind = 'ADVANCE' AND status = 'DRAFT' "
        "AND quote_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_invoice_advance_quote_draft")
    op.execute("ALTER TABLE invoice DROP CONSTRAINT ck_invoice_advance_intent_shape")
    op.execute("ALTER TABLE invoice DROP COLUMN advance_percentage")
    op.execute("ALTER TABLE invoice DROP COLUMN advance_gross_amount")
    op.execute("ALTER TABLE invoice DROP COLUMN advance_input_mode")
