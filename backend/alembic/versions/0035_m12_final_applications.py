"""Add immutable Formal Final Advance applications.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-29
"""

# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The original totals are snapshots for variance display only; old rows
    # intentionally stay NULL so no historical financial value is recomputed.
    for column in (
        "final_original_taxable_amount NUMERIC(18,3)",
        "final_original_vat_amount NUMERIC(18,3)",
        "final_original_gross_amount NUMERIC(18,3)",
    ):
        op.execute(f"ALTER TABLE invoice ADD COLUMN {column}")
    op.execute(
        "CREATE TABLE final_advance_application ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), company_id UUID NOT NULL, "
        "final_invoice_id UUID NOT NULL, advance_invoice_id UUID NOT NULL, sort_order INTEGER NOT NULL, "
        "advance_invoice_date DATE NOT NULL, advance_invoice_number TEXT NOT NULL, "
        "taxable_amount NUMERIC(18,3) NOT NULL, vat_amount NUMERIC(18,3) NOT NULL, gross_amount NUMERIC(18,3) NOT NULL, "
        "base_taxable_amount NUMERIC(18,3) NOT NULL, base_vat_amount NUMERIC(18,3) NOT NULL, base_gross_amount NUMERIC(18,3) NOT NULL, "
        "CONSTRAINT uq_final_advance_application UNIQUE(final_invoice_id, advance_invoice_id), "
        "CONSTRAINT fk_final_application_company FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_final_application_final FOREIGN KEY(final_invoice_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_final_application_advance FOREIGN KEY(advance_invoice_id) REFERENCES invoice(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_final_application_amounts CHECK(taxable_amount >= 0 AND vat_amount >= 0 AND gross_amount = taxable_amount + vat_amount "
        "AND base_taxable_amount >= 0 AND base_vat_amount >= 0 AND base_gross_amount = base_taxable_amount + base_vat_amount))"
    )
    op.execute(
        "CREATE INDEX ix_final_advance_application_company_id ON final_advance_application(company_id)"
    )
    op.execute(
        "CREATE INDEX ix_final_advance_application_final_invoice_id ON final_advance_application(final_invoice_id)"
    )
    op.execute(
        "CREATE TABLE final_advance_application_tax ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), company_id UUID NOT NULL, application_id UUID NOT NULL, sort_order INTEGER NOT NULL, "
        "source_vat_rate_id UUID NOT NULL, source_vat_rate_label TEXT NOT NULL, source_vat_rate_percent NUMERIC(6,3) NOT NULL, "
        "vat_treatment_code TEXT NOT NULL, vat_treatment_effect TEXT NOT NULL, vat_treatment_requires_icp BOOLEAN NOT NULL, "
        "taxable_amount NUMERIC(18,3) NOT NULL, vat_amount NUMERIC(18,3) NOT NULL, gross_amount NUMERIC(18,3) NOT NULL, "
        "base_taxable_amount NUMERIC(18,3) NOT NULL, base_vat_amount NUMERIC(18,3) NOT NULL, base_gross_amount NUMERIC(18,3) NOT NULL, "
        "CONSTRAINT uq_final_application_tax_bucket UNIQUE(application_id, source_vat_rate_id), "
        "CONSTRAINT fk_final_application_tax_company FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_final_application_tax_application FOREIGN KEY(application_id) REFERENCES final_advance_application(id) ON DELETE CASCADE, "
        "CONSTRAINT ck_final_application_tax_amounts CHECK(taxable_amount >= 0 AND vat_amount >= 0 AND gross_amount = taxable_amount + vat_amount "
        "AND base_taxable_amount >= 0 AND base_vat_amount >= 0 AND base_gross_amount = base_taxable_amount + base_vat_amount))"
    )
    op.execute(
        "CREATE INDEX ix_final_advance_application_tax_company_id ON final_advance_application_tax(company_id)"
    )
    op.execute(
        "CREATE INDEX ix_final_advance_application_tax_application_id ON final_advance_application_tax(application_id)"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_final_application_ownership() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM invoice f JOIN invoice a ON a.id = NEW.advance_invoice_id "
        "WHERE f.id = NEW.final_invoice_id AND f.company_id = NEW.company_id AND a.company_id = NEW.company_id "
        "AND f.document_kind = 'FINAL' AND f.status IN ('DRAFT', 'SENT', 'COMPLETED') "
        "AND a.document_kind = 'ADVANCE' AND a.status IN ('SENT', 'COMPLETED') "
        "AND f.quote_id IS NOT NULL AND a.quote_id IS NOT NULL AND f.quote_id = a.quote_id) THEN "
        "RAISE EXCEPTION 'final application ownership/kind mismatch'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_final_application_ownership BEFORE INSERT OR UPDATE ON final_advance_application FOR EACH ROW EXECUTE FUNCTION jai_assert_final_application_ownership()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_final_application_tax_ownership() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM final_advance_application a WHERE a.id = NEW.application_id AND a.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'final application tax ownership mismatch'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_final_application_tax_ownership BEFORE INSERT OR UPDATE ON final_advance_application_tax FOR EACH ROW EXECUTE FUNCTION jai_assert_final_application_tax_ownership()"
    )
    # This DB constraint is the final arbiter for concurrent POSTs.
    op.execute(
        "CREATE UNIQUE INDEX uq_invoice_final_quote_active ON invoice(quote_id) WHERE document_kind = 'FINAL' AND quote_id IS NOT NULL"
    )
    expression = "company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid"
    for table, policy in (
        ("final_advance_application", "final_advance_application_company_isolation"),
        ("final_advance_application_tax", "final_advance_application_tax_company_isolation"),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING ({expression}) WITH CHECK ({expression})"
        )


def downgrade() -> None:
    op.execute("DROP INDEX uq_invoice_final_quote_active")
    op.execute("DROP TABLE final_advance_application_tax")
    op.execute("DROP FUNCTION jai_assert_final_application_tax_ownership()")
    op.execute("DROP TABLE final_advance_application")
    op.execute("DROP FUNCTION jai_assert_final_application_ownership()")
    for column in (
        "final_original_gross_amount",
        "final_original_vat_amount",
        "final_original_taxable_amount",
    ):
        op.execute(f"ALTER TABLE invoice DROP COLUMN {column}")
