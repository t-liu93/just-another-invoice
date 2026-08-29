"""Close M12 Step 2 event ordering and runtime immutability gaps.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import dialect

from alembic import op
from jai.config import get_settings

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role() -> str:
    return dialect().identifier_preparer.quote_identifier(get_settings().postgres_app_user)


def upgrade() -> None:
    for event_type in (
        "INVOICE_UPDATED",
        "INVOICE_ISSUED",
        "INVOICE_STATUS_CHANGED",
        "INVOICE_PAYMENT_CREATED",
        "INVOICE_PAYMENT_UPDATED",
        "INVOICE_PAYMENT_DELETED",
    ):
        op.execute(f"ALTER TYPE documentchaineventtype ADD VALUE IF NOT EXISTS '{event_type}'")
    op.execute("CREATE SEQUENCE document_chain_event_order_seq AS BIGINT")
    op.execute("ALTER TABLE document_chain_event ADD COLUMN event_order BIGINT")
    op.execute(
        "UPDATE document_chain_event SET event_order = nextval('document_chain_event_order_seq')"
    )
    op.execute(
        "ALTER TABLE document_chain_event ALTER COLUMN event_order "
        "SET DEFAULT nextval('document_chain_event_order_seq')"
    )
    op.execute("ALTER TABLE document_chain_event ALTER COLUMN event_order SET NOT NULL")
    op.execute(
        "ALTER TABLE document_chain_event "
        "ADD CONSTRAINT uq_document_chain_event_order UNIQUE (event_order)"
    )
    op.execute(
        "ALTER SEQUENCE document_chain_event_order_seq OWNED BY document_chain_event.event_order"
    )
    op.execute(
        "CREATE INDEX ix_document_chain_event_stable_order "
        "ON document_chain_event (company_id, quote_id, event_order)"
    )
    role = _role()
    # 0030 granted broad table privileges.  This event table intentionally has
    # a narrower, explicit ACL; FK cascades still work inside PostgreSQL for
    # legitimate draft-root cleanup without granting runtime DELETE.
    op.execute(f"REVOKE UPDATE, DELETE ON document_chain_event FROM {role}")
    op.execute(f"GRANT SELECT, INSERT ON document_chain_event TO {role}")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_document_chain_event_ownership() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'document_chain_event is append-only'; END IF; "
        "IF NEW.quote_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM quote q WHERE q.id = NEW.quote_id "
        "AND q.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'document_chain_event quote ownership mismatch'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id "
        "AND i.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'document_chain_event invoice ownership mismatch'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute("DROP TRIGGER trg_document_chain_event_ownership ON document_chain_event")
    op.execute(
        "CREATE TRIGGER trg_document_chain_event_ownership "
        "BEFORE INSERT OR UPDATE ON document_chain_event "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_document_chain_event_ownership()"
    )


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum labels.  Keep the compatible enum
    # superset while removing only this migration's additive storage.
    op.execute("DROP INDEX ix_document_chain_event_stable_order")
    op.execute("ALTER TABLE document_chain_event DROP CONSTRAINT uq_document_chain_event_order")
    # The sequence is OWNED BY this column, so PostgreSQL drops it together
    # with the column.  Do not issue a second DROP SEQUENCE afterwards.
    op.execute("ALTER TABLE document_chain_event DROP COLUMN event_order")
    # Restore the exact 0032 trigger semantics: it protects cross-company
    # ownership but did not yet make the table append-only.  Its broad runtime
    # table ACL is likewise inherited from 0030's default privileges.
    role = _role()
    op.execute("DROP TRIGGER trg_document_chain_event_ownership ON document_chain_event")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_document_chain_event_ownership() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.quote_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM quote q WHERE q.id = NEW.quote_id "
        "AND q.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'document_chain_event quote ownership mismatch'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id "
        "AND i.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'document_chain_event invoice ownership mismatch'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_document_chain_event_ownership "
        "BEFORE INSERT OR UPDATE ON document_chain_event "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_document_chain_event_ownership()"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON document_chain_event TO {role}")
