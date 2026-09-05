"""Add the formal historical-artifact upload enum and database guard.

Revision ID: 0042
Revises: 0041
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _artifact_owner_function(*, enforce_upload_gate: bool) -> str:
    """Return the immutable-owner trigger, optionally with M13's upload gate."""
    upload_gate = ""
    if enforce_upload_gate:
        # Compare as text so this function can be created in the same
        # transaction as ALTER TYPE ... ADD VALUE. PostgreSQL otherwise rejects
        # use of an uncommitted enum label.
        upload_gate = (
            "IF NEW.creation_reason::text = 'UPLOAD' THEN "
            "IF NEW.invoice_id IS NULL OR NEW.artifact_kind::text <> 'FORMAL_DOCUMENT' THEN "
            "RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='uploaded artifact owner must be an issued invoice'; END IF; "
            "PERFORM 1 FROM invoice i WHERE i.id = NEW.invoice_id "
            "AND i.company_id = NEW.company_id AND i.status IN ('SENT','COMPLETED') FOR UPDATE; "
            "IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514', "
            "MESSAGE='uploaded artifact owner must be an issued same-company invoice'; END IF; "
            "IF EXISTS (SELECT 1 FROM document_artifact a WHERE a.invoice_id = NEW.invoice_id) THEN "
            "RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='uploaded artifact requires an invoice with no artifacts'; END IF; "
            "END IF; "
        )
    return (
        "CREATE OR REPLACE FUNCTION jai_assert_document_artifact_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF TG_OP = 'UPDATE' THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='document artifacts are immutable'; END IF; "
        "IF NEW.sha256 <> encode(digest(NEW.pdf_bytes, 'sha256'), 'hex') THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='document artifact hash does not match bytes'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id AND i.company_id = NEW.company_id AND i.status IN ('SENT','COMPLETED')) THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='formal artifact owner must be an issued same-company invoice'; END IF; "
        "IF NEW.refund_payment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payment p WHERE p.id = NEW.refund_payment_id AND p.company_id = NEW.company_id AND p.direction = 'REFUND') THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='refund artifact owner must be a same-company refund payment'; END IF; "
        f"{upload_gate}"
        "RETURN NEW; END $$"
    )


def upgrade() -> None:
    # Enum labels are append-only in production. IF NOT EXISTS keeps a retry
    # after a partially observed deployment harmless without rewriting data.
    op.execute("ALTER TYPE documentartifactreason ADD VALUE IF NOT EXISTS 'UPLOAD'")
    op.execute(_artifact_owner_function(enforce_upload_gate=True))


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum label without a destructive type
    # rewrite. Keep UPLOAD and existing rows intact, while restoring 0041's
    # trigger behaviour for a code downgrade.
    op.execute(_artifact_owner_function(enforce_upload_gate=False))
