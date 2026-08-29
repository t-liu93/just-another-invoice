"""Add normalized source-bound Credit Note corrections.

Revision ID: 0036
Revises: 0035
"""

# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0029 froze nominal rates in the credit basis.  A nominal rate is not an
    # applied rate for reverse-charge/export treatments, so carry the exact
    # persisted invoice tax snapshot forward before Credit Notes exist.
    op.execute(
        "ALTER TABLE invoice_credit_basis_line "
        "ADD COLUMN effective_vat_percent NUMERIC(6,3)"
    )
    # 0030 FORCEs RLS for the runtime role.  This migration runs after that
    # boundary and must backfill every tenant from persisted tax rows, so
    # temporarily disable it within this single transactional migration.
    op.execute("ALTER TABLE invoice_credit_basis_line DISABLE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE invoice_credit_basis_line b SET effective_vat_percent = lt.effective_vat_percent "
        "FROM invoice i, invoice_line_tax lt "
        "WHERE i.id = b.invoice_id AND lt.invoice_line_id = b.invoice_line_id "
        "AND i.tax_mode = 'LINE'"
    )
    op.execute(
        "UPDATE invoice_credit_basis_line b SET effective_vat_percent = t.effective_vat_percent "
        "FROM invoice i JOIN invoice_tax t ON t.invoice_id = i.id "
        "WHERE i.id = b.invoice_id AND i.tax_mode = 'DOCUMENT'"
    )
    op.execute("ALTER TABLE invoice_credit_basis_line ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoice_credit_basis_line FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE invoice_credit_basis_line ADD CONSTRAINT "
        "ck_credit_basis_effective_vat_snapshot CHECK ("
        "vat_rate_id IS NULL OR effective_vat_percent IS NOT NULL)"
    )
    op.execute(
        "CREATE TABLE invoice_correction ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), company_id UUID NOT NULL, "
        "credit_note_id UUID NOT NULL, source_invoice_id UUID NOT NULL, "
        "issued_net_amount NUMERIC(18,3), issued_vat_amount NUMERIC(18,3), issued_gross_amount NUMERIC(18,3), "
        "issued_base_net_amount NUMERIC(18,3), issued_base_vat_amount NUMERIC(18,3), issued_base_gross_amount NUMERIC(18,3), "
        "affects_revenue BOOLEAN, "
        "CONSTRAINT uq_invoice_correction_credit UNIQUE(credit_note_id), "
        "CONSTRAINT fk_invoice_correction_company FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_invoice_correction_credit FOREIGN KEY(credit_note_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_invoice_correction_source FOREIGN KEY(source_invoice_id) REFERENCES invoice(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_invoice_correction_totals CHECK ((issued_gross_amount IS NULL AND issued_net_amount IS NULL AND issued_vat_amount IS NULL AND issued_base_gross_amount IS NULL AND issued_base_net_amount IS NULL AND issued_base_vat_amount IS NULL AND affects_revenue IS NULL) OR (issued_gross_amount IS NOT NULL AND issued_net_amount IS NOT NULL AND issued_vat_amount IS NOT NULL AND issued_base_gross_amount IS NOT NULL AND issued_base_net_amount IS NOT NULL AND issued_base_vat_amount IS NOT NULL AND affects_revenue IS NOT NULL AND issued_net_amount >= 0 AND issued_vat_amount >= 0 AND issued_gross_amount >= 0 AND issued_gross_amount = issued_net_amount + issued_vat_amount AND issued_base_net_amount >= 0 AND issued_base_vat_amount >= 0 AND issued_base_gross_amount >= 0 AND issued_base_gross_amount = issued_base_net_amount + issued_base_vat_amount)))"
    )
    op.execute("CREATE INDEX ix_invoice_correction_company_id ON invoice_correction(company_id)")
    op.execute("CREATE INDEX ix_invoice_correction_source_invoice_id ON invoice_correction(source_invoice_id)")
    op.execute(
        "CREATE TABLE invoice_correction_line ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), company_id UUID NOT NULL, correction_id UUID NOT NULL, source_basis_line_id UUID NOT NULL, sort_order INTEGER NOT NULL, "
        "input_mode TEXT NOT NULL, input_quantity NUMERIC(18,3), input_gross_amount NUMERIC(18,3), quantity NUMERIC(18,3) NOT NULL, "
        "net_amount NUMERIC(18,3) NOT NULL, vat_amount NUMERIC(18,3) NOT NULL, gross_amount NUMERIC(18,3) NOT NULL, "
        "base_net_amount NUMERIC(18,3) NOT NULL, base_vat_amount NUMERIC(18,3) NOT NULL, base_gross_amount NUMERIC(18,3) NOT NULL, "
        "CONSTRAINT uq_correction_basis UNIQUE(correction_id, source_basis_line_id), "
        "CONSTRAINT fk_correction_line_company FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_correction_line_correction FOREIGN KEY(correction_id) REFERENCES invoice_correction(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_correction_line_basis FOREIGN KEY(source_basis_line_id) REFERENCES invoice_credit_basis_line(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_correction_line_input CHECK ((input_mode = 'QUANTITY' AND input_quantity > 0 AND input_gross_amount IS NULL) OR (input_mode = 'GROSS_AMOUNT' AND input_gross_amount > 0 AND input_quantity IS NULL)), "
        "CONSTRAINT ck_correction_line_amounts CHECK (quantity >= 0 AND net_amount >= 0 AND vat_amount >= 0 AND gross_amount = net_amount + vat_amount AND base_net_amount >= 0 AND base_vat_amount >= 0 AND base_gross_amount = base_net_amount + base_vat_amount))"
    )
    op.execute("CREATE INDEX ix_invoice_correction_line_company_id ON invoice_correction_line(company_id)")
    op.execute("CREATE INDEX ix_invoice_correction_line_correction_id ON invoice_correction_line(correction_id)")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_invoice_correction_ownership() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM invoice c JOIN invoice s ON s.id = NEW.source_invoice_id WHERE c.id = NEW.credit_note_id AND c.company_id = NEW.company_id AND s.company_id = NEW.company_id AND c.document_kind = 'CREDIT_NOTE' AND s.document_kind <> 'CREDIT_NOTE' AND s.status IN ('SENT', 'COMPLETED') AND c.quote_id IS NOT DISTINCT FROM s.quote_id) THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invoice correction ownership/kind/source/chain mismatch'; END IF; IF EXISTS (SELECT 1 FROM invoice_correction_line l JOIN invoice_credit_basis_line b ON b.id = l.source_basis_line_id WHERE l.correction_id = NEW.id AND b.invoice_id <> NEW.source_invoice_id) THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invoice correction source/basis mismatch'; END IF; RETURN NEW; END $$"
    )
    op.execute("CREATE TRIGGER trg_invoice_correction_ownership BEFORE INSERT OR UPDATE ON invoice_correction FOR EACH ROW EXECUTE FUNCTION jai_assert_invoice_correction_ownership()")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_invoice_correction_line_ownership() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM invoice_correction c JOIN invoice_credit_basis_line b ON b.id = NEW.source_basis_line_id WHERE c.id = NEW.correction_id AND c.company_id = NEW.company_id AND b.company_id = NEW.company_id AND b.invoice_id = c.source_invoice_id) THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invoice correction line ownership mismatch'; END IF; RETURN NEW; END $$"
    )
    op.execute("CREATE TRIGGER trg_invoice_correction_line_ownership BEFORE INSERT OR UPDATE ON invoice_correction_line FOR EACH ROW EXECUTE FUNCTION jai_assert_invoice_correction_line_ownership()")
    expression = "company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid"
    for table, policy in (("invoice_correction", "invoice_correction_company_isolation"), ("invoice_correction_line", "invoice_correction_line_company_isolation")):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {policy} ON {table} USING ({expression}) WITH CHECK ({expression})")


def downgrade() -> None:
    op.execute("DROP TABLE invoice_correction_line")
    op.execute("DROP FUNCTION jai_assert_invoice_correction_line_ownership()")
    op.execute("DROP TABLE invoice_correction")
    op.execute("DROP FUNCTION jai_assert_invoice_correction_ownership()")
    op.execute(
        "ALTER TABLE invoice_credit_basis_line "
        "DROP CONSTRAINT ck_credit_basis_effective_vat_snapshot"
    )
    op.execute("ALTER TABLE invoice_credit_basis_line DROP COLUMN effective_vat_percent")
