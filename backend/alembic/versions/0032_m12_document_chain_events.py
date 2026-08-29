"""M12 Step 2 append-only document-chain lifecycle events.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-29
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE documentchaineventtype AS ENUM ("
        "'MODE_LOCKED', 'INVOICE_CREATED', 'QUOTE_PAYMENT_CREATED', "
        "'QUOTE_PAYMENT_UPDATED', 'QUOTE_PAYMENT_DELETED', 'INVOICE_DELETED'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "CREATE TABLE document_chain_event ("
        "id UUID NOT NULL DEFAULT gen_random_uuid(), company_id UUID NOT NULL, "
        "quote_id UUID, invoice_id UUID, actor_user_id UUID, "
        "event_type documentchaineventtype NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "CONSTRAINT pk_document_chain_event PRIMARY KEY (id), "
        "CONSTRAINT fk_document_chain_event_company FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_document_chain_event_quote FOREIGN KEY (quote_id) REFERENCES quote(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_document_chain_event_invoice FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_document_chain_event_actor FOREIGN KEY (actor_user_id) REFERENCES \"user\"(id) ON DELETE SET NULL, "
        "CONSTRAINT ck_document_chain_event_root CHECK (quote_id IS NOT NULL OR invoice_id IS NOT NULL), "
        "CONSTRAINT ck_document_chain_event_metadata_object CHECK (jsonb_typeof(metadata_json) = 'object'))"
    )
    op.execute("CREATE INDEX ix_document_chain_event_company_id ON document_chain_event (company_id)")
    op.execute("CREATE INDEX ix_document_chain_event_quote_id ON document_chain_event (quote_id)")
    op.execute("CREATE INDEX ix_document_chain_event_invoice_id ON document_chain_event (invoice_id)")
    op.execute("CREATE INDEX ix_document_chain_event_order ON document_chain_event (company_id, quote_id, occurred_at, id)")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_document_chain_event_ownership() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.quote_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM quote q WHERE q.id = NEW.quote_id AND q.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'document_chain_event quote ownership mismatch'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id AND i.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'document_chain_event invoice ownership mismatch'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_document_chain_event_ownership BEFORE INSERT OR UPDATE ON document_chain_event "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_document_chain_event_ownership()"
    )
    op.execute("ALTER TABLE document_chain_event ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_chain_event FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY document_chain_event_company_isolation ON document_chain_event "
        "USING (company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid) "
        "WITH CHECK (company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE document_chain_event")
    op.execute("DROP FUNCTION jai_assert_document_chain_event_ownership()")
    op.execute("DROP TYPE documentchaineventtype")
