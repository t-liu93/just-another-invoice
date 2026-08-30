"""Add typed correction follow-up provenance.

Revision ID: 0037
Revises: 0036
"""

# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import dialect

from alembic import op
from jai.config import get_settings

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role() -> str:
    return dialect().identifier_preparer.quote_identifier(get_settings().postgres_app_user)


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE invoicerelationtype AS ENUM "
        "('REPLACEMENT_OF', 'COMPENSATES_CREDIT'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    for event_type in (
        "REPLACEMENT_CREATED",
        "COMPENSATING_INVOICE_CREATED",
        "PROJECT_CANCELLATION_CREDIT_CREATED",
    ):
        op.execute(f"ALTER TYPE documentchaineventtype ADD VALUE IF NOT EXISTS '{event_type}'")
    op.execute(
        "CREATE TABLE invoice_relation ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), company_id UUID NOT NULL, "
        "invoice_id UUID NOT NULL, related_credit_note_id UUID NOT NULL, "
        "relation_type invoicerelationtype NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "CONSTRAINT uq_invoice_relation_invoice UNIQUE(invoice_id), "
        "CONSTRAINT uq_invoice_relation_credit UNIQUE(related_credit_note_id), "
        "CONSTRAINT fk_invoice_relation_company FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_invoice_relation_invoice FOREIGN KEY(invoice_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_invoice_relation_credit FOREIGN KEY(related_credit_note_id) REFERENCES invoice(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_invoice_relation_distinct CHECK(invoice_id <> related_credit_note_id))"
    )
    op.execute("CREATE INDEX ix_invoice_relation_company_id ON invoice_relation(company_id)")
    op.execute("CREATE INDEX ix_invoice_relation_invoice_id ON invoice_relation(invoice_id)")
    op.execute(
        "CREATE INDEX ix_invoice_relation_related_credit_note_id "
        "ON invoice_relation(related_credit_note_id)"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_invoice_relation_ownership() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM invoice p JOIN invoice c ON c.id = NEW.related_credit_note_id "
        "WHERE p.id = NEW.invoice_id AND p.company_id = NEW.company_id "
        "AND c.company_id = NEW.company_id AND p.document_kind IN ('STANDARD', 'ADVANCE') "
        "AND p.status = 'DRAFT' AND c.document_kind = 'CREDIT_NOTE' "
        "AND c.status IN ('SENT', 'COMPLETED') "
        "AND p.quote_id IS NOT DISTINCT FROM c.quote_id) THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'invoice relation ownership/kind/status/chain mismatch'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_invoice_relation_ownership BEFORE INSERT OR UPDATE ON invoice_relation "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_invoice_relation_ownership()"
    )
    expression = "company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid"
    op.execute("ALTER TABLE invoice_relation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoice_relation FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY invoice_relation_company_isolation ON invoice_relation "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
    role = _role()
    op.execute(f"REVOKE UPDATE, DELETE ON invoice_relation FROM {role}")
    op.execute(f"GRANT SELECT, INSERT ON invoice_relation TO {role}")


def downgrade() -> None:
    op.execute("DROP TABLE invoice_relation")
    op.execute("DROP FUNCTION jai_assert_invoice_relation_ownership()")
    op.execute("DROP TYPE invoicerelationtype")
    # PostgreSQL enum labels are intentionally retained; removing labels is
    # unsafe and the compatible superset does not affect the downgraded app.
