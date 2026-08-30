"""Add append-only refund lifecycle event labels.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for event_type in ("REFUND_CREATED", "REFUND_UPDATED", "REFUND_DELETED"):
        op.execute(f"ALTER TYPE documentchaineventtype ADD VALUE IF NOT EXISTS '{event_type}'")
    op.execute(
        "ALTER TABLE payment ADD CONSTRAINT ck_payment_positive_amounts_m12 "
        "CHECK (amount > 0 AND base_amount > 0 AND exchange_rate > 0)"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_payment_m12_context() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF TG_OP = 'UPDATE' THEN "
        # Lock the same parent row as the PaymentTax trigger before checking
        # child rows.  A child insert that won the lock commits before this
        # statement rechecks; a direction change that won it makes the child
        # insert fail after its own recheck.
        "PERFORM 1 FROM payment WHERE id = OLD.id FOR UPDATE; "
        "END IF; "
        "IF NEW.direction = 'REFUND' THEN "
        "IF NOT EXISTS (SELECT 1 FROM invoice c JOIN invoice_correction x "
        "ON x.credit_note_id = c.id AND x.company_id = NEW.company_id "
        "WHERE c.id = NEW.credit_note_id AND c.company_id = NEW.company_id "
        "AND c.document_kind = 'CREDIT_NOTE' AND c.status IN ('SENT', 'COMPLETED') "
        "AND c.issued_at IS NOT NULL) THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'refund must reference an issued same-company Credit Note'; "
        "END IF; "
        "IF TG_OP = 'UPDATE' AND EXISTS "
        "(SELECT 1 FROM payment_tax t WHERE t.payment_id = OLD.id) THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'refund cannot retain payment tax rows'; END IF; "
        "ELSE "
        "IF NEW.credit_note_id IS NOT NULL THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'incoming payment cannot reference a Credit Note'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id "
        "AND i.company_id = NEW.company_id "
        "AND i.document_kind <> 'CREDIT_NOTE') THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'incoming payment invoice context mismatch'; END IF; "
        "IF NEW.quote_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM quote q WHERE q.id = NEW.quote_id "
        "AND q.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'incoming payment quote context mismatch'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NEW.quote_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id "
        "AND i.quote_id = NEW.quote_id AND i.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'incoming payment invoice/quote chain mismatch'; END IF; "
        "END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_payment_m12_context BEFORE INSERT OR UPDATE ON payment "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_payment_m12_context()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_payment_tax_not_refund() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ DECLARE parent_direction paymentdirection; BEGIN "
        # Canonical cross-table lock: always lock the parent Payment first,
        # then validate its current direction.  This serializes tax writes
        # with an INCOMING->REFUND transition under READ COMMITTED.
        "SELECT p.direction INTO parent_direction FROM payment p "
        "WHERE p.id = NEW.payment_id FOR UPDATE; "
        "IF NOT FOUND OR parent_direction <> 'INCOMING' THEN "
        "RAISE EXCEPTION USING ERRCODE = '23514', "
        "MESSAGE = 'refund cannot have payment tax rows'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_payment_tax_not_refund BEFORE INSERT OR UPDATE ON payment_tax "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_payment_tax_not_refund()"
    )
    tenant = "company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid"
    op.execute("ALTER TABLE payment ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY payment_company_isolation ON payment "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )
    payment_tax_tenant = (
        "EXISTS (SELECT 1 FROM payment p WHERE p.id = payment_id "
        "AND p.company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid)"
    )
    op.execute("ALTER TABLE payment_tax ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY payment_tax_company_isolation ON payment_tax "
        f"USING ({payment_tax_tenant}) WITH CHECK ({payment_tax_tenant})"
    )


def downgrade() -> None:
    # PostgreSQL enum labels deliberately remain: removing a label requires a
    # destructive type rewrite and the compatible superset is safe for 0037.
    op.execute("DROP TRIGGER trg_payment_tax_not_refund ON payment_tax")
    op.execute("DROP FUNCTION jai_assert_payment_tax_not_refund()")
    op.execute("DROP TRIGGER trg_payment_m12_context ON payment")
    op.execute("DROP FUNCTION jai_assert_payment_m12_context()")
    op.execute("DROP POLICY payment_tax_company_isolation ON payment_tax")
    op.execute("ALTER TABLE payment_tax DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY payment_company_isolation ON payment")
    op.execute("ALTER TABLE payment DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment DROP CONSTRAINT ck_payment_positive_amounts_m12")
