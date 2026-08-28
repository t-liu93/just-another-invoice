"""M12 document foundation, compatibility backfill and credit numbering base.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-28

This is deliberately additive.  It never re-prices old documents, recalculates
historical taxes, or rewrites paid/lifecycle status.  The small cache columns
are seeded from persisted invoice/payment values only.
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE invoicepaidstatus ADD VALUE IF NOT EXISTS 'NOT_APPLICABLE'")
    for type_name, values in {
        "invoicedocumentkind": "'STANDARD', 'ADVANCE', 'FINAL', 'CREDIT_NOTE'",
        "quotesettlementmode": "'UNSET', 'DIRECT_INVOICE', 'RECEIPT_ONLY', 'FORMAL_ADVANCE'",
        "paymentdirection": "'INCOMING', 'REFUND'",
        "invoicesettlementstatus": "'OPEN', 'PARTIALLY_SETTLED', 'SETTLED', 'REFUND_DUE'",
        "invoicecreditstatus": "'NOT_CREDITED', 'PARTIALLY_CREDITED', 'CREDITED'",
        "partysnapshotprovenance": "'NATIVE_ISSUE', 'MIGRATED_CURRENT_STATE'",
    }.items():
        op.execute(
            f"DO $$ BEGIN CREATE TYPE {type_name} AS ENUM ({values}); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )

    op.execute("ALTER TABLE quote ADD COLUMN settlement_mode quotesettlementmode NOT NULL DEFAULT 'UNSET'")
    op.execute("ALTER TABLE quote ADD COLUMN settlement_mode_locked_at TIMESTAMPTZ")

    op.execute("ALTER TABLE invoice ADD COLUMN document_kind invoicedocumentkind NOT NULL DEFAULT 'STANDARD'")
    op.execute("ALTER TABLE invoice ADD COLUMN quote_id UUID")
    op.execute("ALTER TABLE invoice ADD COLUMN supply_or_advance_date DATE")
    op.execute("ALTER TABLE invoice ADD COLUMN issued_at TIMESTAMPTZ")
    op.execute("ALTER TABLE invoice ADD COLUMN issued_by_user_id UUID")
    op.execute("ALTER TABLE invoice ADD COLUMN payable_before_payments NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN incoming_payment_total NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN credited_total NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN refunded_total NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN refund_due_amount NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN settlement_status invoicesettlementstatus NOT NULL DEFAULT 'OPEN'")
    op.execute("ALTER TABLE invoice ADD COLUMN credit_status invoicecreditstatus NOT NULL DEFAULT 'NOT_CREDITED'")
    op.execute("ALTER TABLE invoice ADD COLUMN base_payable_before_payments NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN base_incoming_payment_total NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN base_credited_total NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN base_refunded_total NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD COLUMN base_refund_due_amount NUMERIC(18,3) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE invoice ADD CONSTRAINT fk_invoice_quote_m12 FOREIGN KEY (quote_id) REFERENCES quote(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE invoice ADD CONSTRAINT fk_invoice_issued_by_m12 FOREIGN KEY (issued_by_user_id) REFERENCES \"user\"(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX ix_invoice_quote_id ON invoice (quote_id)")

    # Compatibility only: values come exclusively from old persisted document
    # state and payment rows; no price/tax/report recalculation is performed.
    op.execute("UPDATE invoice SET quote_id = quote.id FROM quote WHERE quote.converted_invoice_id = invoice.id")
    op.execute("UPDATE invoice SET payable_before_payments = total_incl_vat, base_payable_before_payments = base_total_incl_vat")
    op.execute(
        "UPDATE invoice i SET incoming_payment_total = p.amount, base_incoming_payment_total = p.base_amount "
        "FROM (SELECT invoice_id, COALESCE(sum(amount), 0) AS amount, COALESCE(sum(base_amount), 0) AS base_amount "
        "FROM payment GROUP BY invoice_id) p WHERE p.invoice_id = i.id"
    )
    op.execute(
        "UPDATE invoice SET settlement_status = CASE paid_status "
        "WHEN 'PAID' THEN 'SETTLED'::invoicesettlementstatus "
        "WHEN 'PARTIALLY_PAID' THEN 'PARTIALLY_SETTLED'::invoicesettlementstatus "
        "ELSE 'OPEN'::invoicesettlementstatus END"
    )
    op.execute(
        "UPDATE quote q SET settlement_mode = CASE "
        "WHEN EXISTS (SELECT 1 FROM payment p WHERE p.quote_id = q.id) THEN 'RECEIPT_ONLY'::quotesettlementmode "
        "WHEN q.converted_invoice_id IS NOT NULL THEN 'DIRECT_INVOICE'::quotesettlementmode "
        "ELSE 'UNSET'::quotesettlementmode END"
    )

    op.execute("ALTER TABLE payment ADD COLUMN direction paymentdirection NOT NULL DEFAULT 'INCOMING'")
    op.execute("ALTER TABLE payment ADD COLUMN credit_note_id UUID")
    op.execute("ALTER TABLE payment ADD CONSTRAINT fk_payment_credit_note_m12 FOREIGN KEY (credit_note_id) REFERENCES invoice(id) ON DELETE RESTRICT")
    op.execute("CREATE INDEX ix_payment_credit_note_id ON payment (credit_note_id)")
    op.execute("ALTER TABLE payment DROP CONSTRAINT ck_payment_document_link")
    op.execute(
        "ALTER TABLE payment ADD CONSTRAINT ck_payment_direction_context CHECK ("
        "(direction = 'INCOMING' AND credit_note_id IS NULL AND (invoice_id IS NOT NULL OR quote_id IS NOT NULL)) OR "
        "(direction = 'REFUND' AND credit_note_id IS NOT NULL AND invoice_id IS NULL AND quote_id IS NULL))"
    )

    op.execute(
        "CREATE TABLE invoice_party_snapshot ("
        "id UUID NOT NULL DEFAULT gen_random_uuid(), company_id UUID NOT NULL, invoice_id UUID NOT NULL, "
        "provenance partysnapshotprovenance NOT NULL, seller_name TEXT NOT NULL, seller_legal_name TEXT, seller_vat_id TEXT, seller_coc_number TEXT, seller_email TEXT, seller_phone TEXT, seller_address JSONB NOT NULL, "
        "buyer_name TEXT NOT NULL, buyer_company_name TEXT, buyer_contact_name TEXT, buyer_vat_id TEXT, buyer_email TEXT, buyer_phone TEXT, buyer_address JSONB NOT NULL, locale TEXT NOT NULL, logo_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "CONSTRAINT pk_invoice_party_snapshot PRIMARY KEY (id), CONSTRAINT uq_invoice_party_snapshot_invoice UNIQUE (invoice_id), "
        "CONSTRAINT fk_invoice_party_snapshot_company FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_invoice_party_snapshot_invoice FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_invoice_party_snapshot_logo FOREIGN KEY (logo_id) REFERENCES binary_asset(id) ON DELETE RESTRICT)"
    )
    op.execute("CREATE INDEX ix_invoice_party_snapshot_company_id ON invoice_party_snapshot (company_id)")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_party_snapshot_ownership() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id AND i.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'invoice_party_snapshot company/invoice ownership mismatch'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_party_snapshot_ownership BEFORE INSERT OR UPDATE ON invoice_party_snapshot "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_party_snapshot_ownership()"
    )
    # Existing issues have no trustworthy actor/timestamp.  Snapshot current
    # master data explicitly as migrated provenance, without inventing either.
    op.execute(
        "INSERT INTO invoice_party_snapshot (company_id, invoice_id, provenance, seller_name, seller_legal_name, seller_vat_id, seller_coc_number, seller_email, seller_phone, seller_address, buyer_name, buyer_company_name, buyer_contact_name, buyer_vat_id, buyer_email, buyer_phone, buyer_address, locale, logo_id) "
        "SELECT i.company_id, i.id, 'MIGRATED_CURRENT_STATE', c.name, c.legal_name, c.vat_id, c.coc_number, c.email, c.phone, "
        "jsonb_build_object('street', c.street, 'house_number', c.house_number, 'house_number_addition', c.house_number_addition, 'postal_code', c.postal_code, 'city', c.city, 'province', c.province, 'country_code', c.country_code), "
        "cu.name, cu.company_name, cu.contact_name, cu.vat_id, cu.email, cu.phone, "
        "COALESCE((SELECT jsonb_build_object('street', a.street, 'house_number', a.house_number, 'house_number_addition', a.house_number_addition, 'postal_code', a.postal_code, 'city', a.city, 'province', a.province, 'country_code', a.country_code) FROM address a WHERE a.customer_id = cu.id AND a.type = 'BILLING' LIMIT 1), '{}'::jsonb), "
        "COALESCE(cu.locale, (SELECT s.value ->> 'locale' FROM setting s WHERE s.level = 'COMPANY' AND s.scope_id = c.id AND s.key = 'document.default_locale'), 'en'), c.logo_id FROM invoice i JOIN company c ON c.id = i.company_id JOIN customer cu ON cu.id = i.customer_id WHERE i.status IN ('SENT', 'COMPLETED')"
    )

    op.execute(
        "CREATE TABLE invoice_credit_basis_line ("
        "id UUID NOT NULL DEFAULT gen_random_uuid(), company_id UUID NOT NULL, invoice_id UUID NOT NULL, invoice_line_id UUID NOT NULL, sort_order INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, quantity NUMERIC(18,3) NOT NULL, unit_name TEXT, vat_rate_id UUID, vat_rate_label TEXT, vat_rate_percent NUMERIC(6,3), vat_treatment_code TEXT NOT NULL, vat_treatment_effect TEXT NOT NULL, vat_treatment_requires_icp BOOLEAN NOT NULL, net_amount NUMERIC(18,3) NOT NULL, vat_amount NUMERIC(18,3) NOT NULL, gross_amount NUMERIC(18,3) NOT NULL, base_net_amount NUMERIC(18,3) NOT NULL, base_vat_amount NUMERIC(18,3) NOT NULL, base_gross_amount NUMERIC(18,3) NOT NULL, "
        "CONSTRAINT pk_invoice_credit_basis_line PRIMARY KEY (id), CONSTRAINT uq_credit_basis_invoice_line UNIQUE (invoice_line_id), "
        "CONSTRAINT fk_credit_basis_company FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_credit_basis_invoice FOREIGN KEY (invoice_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_credit_basis_line FOREIGN KEY (invoice_line_id) REFERENCES invoice_line(id) ON DELETE CASCADE)"
    )
    op.execute("CREATE INDEX ix_invoice_credit_basis_line_company_id ON invoice_credit_basis_line (company_id)")
    op.execute("CREATE INDEX ix_invoice_credit_basis_line_invoice_id ON invoice_credit_basis_line (invoice_id)")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_credit_basis_ownership() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id AND i.company_id = NEW.company_id) THEN "
        "RAISE EXCEPTION 'invoice_credit_basis_line company/invoice ownership mismatch'; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM invoice_line l WHERE l.id = NEW.invoice_line_id AND l.invoice_id = NEW.invoice_id) THEN "
        "RAISE EXCEPTION 'invoice_credit_basis_line invoice/line ownership mismatch'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_credit_basis_ownership BEFORE INSERT OR UPDATE ON invoice_credit_basis_line "
        "FOR EACH ROW EXECUTE FUNCTION jai_assert_credit_basis_ownership()"
    )
    op.execute(
        "ALTER TABLE invoice_credit_basis_line ADD CONSTRAINT ck_credit_basis_nonnegative CHECK ("
        "quantity >= 0 AND net_amount >= 0 AND vat_amount >= 0 AND gross_amount >= 0 AND "
        "base_net_amount >= 0 AND base_vat_amount >= 0 AND base_gross_amount >= 0)"
    )
    op.execute(
        "ALTER TABLE invoice_credit_basis_line ADD CONSTRAINT ck_credit_basis_gross_conservation CHECK ("
        "gross_amount = net_amount + vat_amount AND base_gross_amount = base_net_amount + base_vat_amount)"
    )
    # DOCUMENT-mode line VAT is intentionally zero in the old line snapshots.
    # Allocate the persisted InvoiceTax amount over persisted taxable line bases
    # at the NUMERIC(18,3) storage scale (sort_order, id).  Historical source
    # snapshots can legitimately have a third decimal; using minor units here
    # would silently alter the immutable credit entitlement.  This neither
    # re-prices nor reads today's VAT dictionary.
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM invoice i JOIN invoice_line l ON l.invoice_id = i.id "
        "JOIN invoice_tax t ON t.invoice_id = i.id "
        "WHERE i.status IN ('SENT', 'COMPLETED') AND i.tax_mode = 'DOCUMENT' "
        "GROUP BY i.id, t.tax_amount HAVING sum(l.taxable_amount) = 0 AND t.tax_amount <> 0"
        ") THEN RAISE EXCEPTION 'Cannot backfill DOCUMENT credit basis: zero net with non-zero VAT'; END IF; END $$"
    )
    op.execute(
        "INSERT INTO invoice_credit_basis_line (company_id, invoice_id, invoice_line_id, sort_order, name, description, quantity, unit_name, vat_rate_id, vat_rate_label, vat_rate_percent, vat_treatment_code, vat_treatment_effect, vat_treatment_requires_icp, net_amount, vat_amount, gross_amount, base_net_amount, base_vat_amount, base_gross_amount) "
        "WITH line_rows AS (SELECT i.company_id, i.id AS invoice_id, i.taxable_amount AS invoice_net, i.vat_total AS invoice_vat, i.base_taxable_amount, i.base_vat_total, i.vat_treatment_code, i.vat_treatment_effect, i.vat_treatment_requires_icp, l.id AS invoice_line_id, l.sort_order, l.name, l.description, l.quantity, l.unit_name, l.vat_rate_id, l.vat_rate_label, l.vat_rate_percent, l.taxable_amount, l.vat_total, l.total_incl_vat FROM invoice i JOIN invoice_line l ON l.invoice_id = i.id WHERE i.status IN ('SENT', 'COMPLETED') AND i.tax_mode <> 'DOCUMENT'), allocated AS (SELECT *, CASE WHEN invoice_net = 0 THEN 0 ELSE floor((base_taxable_amount * 1000) * taxable_amount / invoice_net)::bigint END AS base_net_floor, CASE WHEN invoice_net = 0 THEN 0 ELSE mod((base_taxable_amount * 1000 * taxable_amount)::numeric, invoice_net) END AS base_net_remainder, CASE WHEN invoice_vat = 0 THEN 0 ELSE floor((base_vat_total * 1000) * vat_total / invoice_vat)::bigint END AS base_vat_floor, CASE WHEN invoice_vat = 0 THEN 0 ELSE mod((base_vat_total * 1000 * vat_total)::numeric, invoice_vat) END AS base_vat_remainder FROM line_rows), ranked AS (SELECT *, row_number() OVER (PARTITION BY invoice_id ORDER BY base_net_remainder DESC, sort_order, invoice_line_id) AS base_net_rank, row_number() OVER (PARTITION BY invoice_id ORDER BY base_vat_remainder DESC, sort_order, invoice_line_id) AS base_vat_rank, ((base_taxable_amount * 1000)::bigint - sum(base_net_floor) OVER (PARTITION BY invoice_id)) AS base_net_residual, ((base_vat_total * 1000)::bigint - sum(base_vat_floor) OVER (PARTITION BY invoice_id)) AS base_vat_residual FROM allocated) "
        "SELECT company_id, invoice_id, invoice_line_id, sort_order, name, description, quantity, unit_name, vat_rate_id, vat_rate_label, vat_rate_percent, vat_treatment_code, vat_treatment_effect, vat_treatment_requires_icp, taxable_amount, vat_total, total_incl_vat, ((base_net_floor + CASE WHEN base_net_rank <= base_net_residual THEN 1 ELSE 0 END) / 1000.0)::numeric, ((base_vat_floor + CASE WHEN base_vat_rank <= base_vat_residual THEN 1 ELSE 0 END) / 1000.0)::numeric, (((base_net_floor + CASE WHEN base_net_rank <= base_net_residual THEN 1 ELSE 0 END) + (base_vat_floor + CASE WHEN base_vat_rank <= base_vat_residual THEN 1 ELSE 0 END)) / 1000.0)::numeric FROM ranked"
    )
    op.execute(
        "INSERT INTO invoice_credit_basis_line (company_id, invoice_id, invoice_line_id, sort_order, name, description, quantity, unit_name, vat_rate_id, vat_rate_label, vat_rate_percent, vat_treatment_code, vat_treatment_effect, vat_treatment_requires_icp, net_amount, vat_amount, gross_amount, base_net_amount, base_vat_amount, base_gross_amount) "
        "WITH document_rows AS (SELECT i.company_id, i.id AS invoice_id, i.base_taxable_amount, i.base_vat_total, i.vat_treatment_code, i.vat_treatment_effect, i.vat_treatment_requires_icp, l.id AS invoice_line_id, l.sort_order, l.name, l.description, l.quantity, l.unit_name, l.taxable_amount, t.vat_rate_id, t.vat_rate_label, t.vat_rate_percent, t.tax_amount, sum(l.taxable_amount) OVER (PARTITION BY i.id) AS net_total FROM invoice i JOIN invoice_line l ON l.invoice_id = i.id JOIN invoice_tax t ON t.invoice_id = i.id WHERE i.status IN ('SENT', 'COMPLETED') AND i.tax_mode = 'DOCUMENT'), allocated AS (SELECT *, CASE WHEN net_total = 0 THEN 0 ELSE floor((tax_amount * 1000) * taxable_amount / net_total)::bigint END AS vat_floor, CASE WHEN net_total = 0 THEN 0 ELSE mod((tax_amount * 1000 * taxable_amount)::numeric, net_total) END AS vat_remainder, CASE WHEN net_total = 0 THEN 0 ELSE floor((base_taxable_amount * 1000) * taxable_amount / net_total)::bigint END AS base_net_floor, CASE WHEN net_total = 0 THEN 0 ELSE mod((base_taxable_amount * 1000 * taxable_amount)::numeric, net_total) END AS base_net_remainder, CASE WHEN net_total = 0 THEN 0 ELSE floor((base_vat_total * 1000) * taxable_amount / net_total)::bigint END AS base_vat_floor, CASE WHEN net_total = 0 THEN 0 ELSE mod((base_vat_total * 1000 * taxable_amount)::numeric, net_total) END AS base_vat_remainder FROM document_rows), ranked AS (SELECT *, row_number() OVER (PARTITION BY invoice_id ORDER BY vat_remainder DESC, sort_order, invoice_line_id) AS vat_rank, row_number() OVER (PARTITION BY invoice_id ORDER BY base_net_remainder DESC, sort_order, invoice_line_id) AS base_net_rank, row_number() OVER (PARTITION BY invoice_id ORDER BY base_vat_remainder DESC, sort_order, invoice_line_id) AS base_vat_rank, ((tax_amount * 1000)::bigint - sum(vat_floor) OVER (PARTITION BY invoice_id)) AS vat_residual, ((base_taxable_amount * 1000)::bigint - sum(base_net_floor) OVER (PARTITION BY invoice_id)) AS base_net_residual, ((base_vat_total * 1000)::bigint - sum(base_vat_floor) OVER (PARTITION BY invoice_id)) AS base_vat_residual FROM allocated) "
        "SELECT company_id, invoice_id, invoice_line_id, sort_order, name, description, quantity, unit_name, vat_rate_id, vat_rate_label, vat_rate_percent, vat_treatment_code, vat_treatment_effect, vat_treatment_requires_icp, taxable_amount, ((vat_floor + CASE WHEN vat_rank <= vat_residual THEN 1 ELSE 0 END) / 1000.0)::numeric, (taxable_amount + ((vat_floor + CASE WHEN vat_rank <= vat_residual THEN 1 ELSE 0 END) / 1000.0)::numeric), ((base_net_floor + CASE WHEN base_net_rank <= base_net_residual THEN 1 ELSE 0 END) / 1000.0)::numeric, ((base_vat_floor + CASE WHEN base_vat_rank <= base_vat_residual THEN 1 ELSE 0 END) / 1000.0)::numeric, (((base_net_floor + CASE WHEN base_net_rank <= base_net_residual THEN 1 ELSE 0 END) + (base_vat_floor + CASE WHEN base_vat_rank <= base_vat_residual THEN 1 ELSE 0 END)) / 1000.0)::numeric FROM ranked"
    )
    # RLS follows the compatibility inserts: a migration must be able to
    # backfill every legacy company before ordinary tenant sessions are
    # constrained by the mandatory context policy.
    for table, policy in (
        ("invoice_party_snapshot", "invoice_party_snapshot_company_isolation"),
        ("invoice_credit_basis_line", "invoice_credit_basis_line_company_isolation"),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy} ON {table} "
            "USING (company_id = current_setting('jai.company_id', true)::uuid) "
            "WITH CHECK (company_id = current_setting('jai.company_id', true)::uuid)"
        )


def downgrade() -> None:
    # 0029 is an additive compatibility foundation.  A deliberate downgrade
    # removes only its representational additions; upgrading again rebuilds
    # migrated legacy snapshots from the unchanged source document rows.
    op.execute("DROP TABLE invoice_credit_basis_line")
    op.execute("DROP FUNCTION jai_assert_credit_basis_ownership()")
    op.execute("DROP TABLE invoice_party_snapshot")
    op.execute("DROP FUNCTION jai_assert_party_snapshot_ownership()")
    op.execute("ALTER TABLE payment DROP CONSTRAINT ck_payment_direction_context")
    op.execute("ALTER TABLE payment ADD CONSTRAINT ck_payment_document_link CHECK (invoice_id IS NOT NULL OR quote_id IS NOT NULL)")
    op.execute("DROP INDEX ix_payment_credit_note_id")
    op.execute("ALTER TABLE payment DROP CONSTRAINT fk_payment_credit_note_m12")
    op.execute("ALTER TABLE payment DROP COLUMN credit_note_id")
    op.execute("ALTER TABLE payment DROP COLUMN direction")
    op.execute("DROP INDEX ix_invoice_quote_id")
    op.execute("ALTER TABLE invoice DROP CONSTRAINT fk_invoice_issued_by_m12")
    op.execute("ALTER TABLE invoice DROP CONSTRAINT fk_invoice_quote_m12")
    for column in ("base_refund_due_amount", "base_refunded_total", "base_credited_total", "base_incoming_payment_total", "base_payable_before_payments", "credit_status", "settlement_status", "refund_due_amount", "refunded_total", "credited_total", "incoming_payment_total", "payable_before_payments", "issued_by_user_id", "issued_at", "supply_or_advance_date", "quote_id", "document_kind"):
        op.execute(f"ALTER TABLE invoice DROP COLUMN {column}")
    op.execute("ALTER TABLE quote DROP COLUMN settlement_mode_locked_at")
    op.execute("ALTER TABLE quote DROP COLUMN settlement_mode")
    for type_name in ("partysnapshotprovenance", "invoicecreditstatus", "invoicesettlementstatus", "paymentdirection", "quotesettlementmode", "invoicedocumentkind"):
        op.execute(f"DROP TYPE {type_name}")
