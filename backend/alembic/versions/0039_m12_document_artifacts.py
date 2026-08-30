"""Retain exact M12 formal-document and refund-confirmation PDF bytes.

Revision ID: 0039
Revises: 0038
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``gen_random_uuid`` is built into modern PostgreSQL, but ``digest`` is
    # supplied by pgcrypto.  The byte/hash integrity trigger below must not
    # silently depend on an extension installed out-of-band.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("ALTER TYPE emailrelatedtype ADD VALUE IF NOT EXISTS 'REFUND'")
    op.execute("DO $$ BEGIN CREATE TYPE documentartifactkind AS ENUM ('FORMAL_DOCUMENT', 'REFUND_CONFIRMATION'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE TYPE documentartifactreason AS ENUM ('DOWNLOAD', 'SEND'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute(
        "CREATE TABLE document_artifact ("
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), company_id UUID NOT NULL, "
        "invoice_id UUID, refund_payment_id UUID, artifact_kind documentartifactkind NOT NULL, "
        "pdf_bytes BYTEA NOT NULL, sha256 VARCHAR(64) NOT NULL, render_fingerprint VARCHAR(64) NOT NULL, locale VARCHAR(5) NOT NULL, "
        "filename TEXT NOT NULL, creation_reason documentartifactreason NOT NULL, "
        "renderer_version VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "CONSTRAINT fk_document_artifact_company FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_document_artifact_invoice FOREIGN KEY(invoice_id) REFERENCES invoice(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_document_artifact_refund FOREIGN KEY(refund_payment_id) REFERENCES payment(id) ON DELETE CASCADE, "
        "CONSTRAINT ck_document_artifact_one_owner CHECK ((invoice_id IS NOT NULL AND refund_payment_id IS NULL AND artifact_kind = 'FORMAL_DOCUMENT') OR (invoice_id IS NULL AND refund_payment_id IS NOT NULL AND artifact_kind = 'REFUND_CONFIRMATION')), "
        "CONSTRAINT ck_document_artifact_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), "
        "CONSTRAINT ck_document_artifact_render_fingerprint CHECK (render_fingerprint ~ '^[0-9a-f]{64}$'), "
        "CONSTRAINT ck_document_artifact_nonempty_bytes CHECK (octet_length(pdf_bytes) > 0), "
        "CONSTRAINT uq_document_artifact_invoice_hash UNIQUE(invoice_id, artifact_kind, sha256), "
        "CONSTRAINT uq_document_artifact_refund_hash UNIQUE(refund_payment_id, artifact_kind, sha256), "
        "CONSTRAINT uq_document_artifact_invoice_render UNIQUE(invoice_id, artifact_kind, locale, renderer_version, render_fingerprint), "
        "CONSTRAINT uq_document_artifact_refund_render UNIQUE(refund_payment_id, artifact_kind, locale, renderer_version, render_fingerprint))"
    )
    op.execute("CREATE INDEX ix_document_artifact_company_id ON document_artifact(company_id)")
    op.execute("CREATE INDEX ix_document_artifact_invoice_id ON document_artifact(invoice_id)")
    op.execute("CREATE INDEX ix_document_artifact_refund_payment_id ON document_artifact(refund_payment_id)")
    op.execute("CREATE INDEX ix_document_artifact_company_created ON document_artifact(company_id, created_at)")
    op.execute(
        "CREATE OR REPLACE FUNCTION jai_assert_document_artifact_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF TG_OP = 'UPDATE' THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='document artifacts are immutable'; END IF; "
        "IF NEW.sha256 <> encode(digest(NEW.pdf_bytes, 'sha256'), 'hex') THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='document artifact hash does not match bytes'; END IF; "
        "IF NEW.invoice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM invoice i WHERE i.id = NEW.invoice_id AND i.company_id = NEW.company_id AND i.status IN ('SENT','COMPLETED')) THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='formal artifact owner must be an issued same-company invoice'; END IF; "
        "IF NEW.refund_payment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payment p WHERE p.id = NEW.refund_payment_id AND p.company_id = NEW.company_id AND p.direction = 'REFUND') THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='refund artifact owner must be a same-company refund payment'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute("CREATE TRIGGER trg_document_artifact_owner BEFORE INSERT OR UPDATE ON document_artifact FOR EACH ROW EXECUTE FUNCTION jai_assert_document_artifact_owner()")
    op.execute("ALTER TABLE document_artifact ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_artifact FORCE ROW LEVEL SECURITY")
    tenant = "company_id = NULLIF(current_setting('jai.company_id', true), '')::uuid"
    # Do not grant a runtime DELETE policy.  Retained bytes are immutable
    # history; owner FK cascades remain database-managed retention semantics.
    op.execute("CREATE POLICY document_artifact_company_select ON document_artifact FOR SELECT USING (" + tenant + ")")
    op.execute("CREATE POLICY document_artifact_company_insert ON document_artifact FOR INSERT WITH CHECK (" + tenant + ")")
    # UPDATE is deliberately visible to the immutability trigger so raw writes
    # fail loudly instead of silently affecting zero rows; DELETE has no policy.
    op.execute("CREATE POLICY document_artifact_company_update ON document_artifact FOR UPDATE USING (" + tenant + ") WITH CHECK (" + tenant + ")")
    op.execute("ALTER TABLE email_log ADD COLUMN artifact_id UUID")
    op.execute("ALTER TABLE email_log ADD CONSTRAINT fk_email_log_artifact FOREIGN KEY(artifact_id) REFERENCES document_artifact(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX ix_email_log_artifact_id ON email_log(artifact_id)")
    # Email audit became tenant-visible in M12.  Existing rows remain untouched;
    # new/read paths always set the request-local company GUC.
    op.execute("ALTER TABLE email_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_log FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY email_log_company_isolation ON email_log USING (" + tenant + ") WITH CHECK (" + tenant + ")")


def downgrade() -> None:
    op.execute("DROP POLICY email_log_company_isolation ON email_log")
    op.execute("ALTER TABLE email_log DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX ix_email_log_artifact_id")
    op.execute("ALTER TABLE email_log DROP CONSTRAINT fk_email_log_artifact")
    op.execute("ALTER TABLE email_log DROP COLUMN artifact_id")
    op.execute("DROP TABLE document_artifact")
    op.execute("DROP FUNCTION jai_assert_document_artifact_owner()")
    op.execute("DROP TYPE documentartifactreason")
    op.execute("DROP TYPE documentartifactkind")
    # enum value REFUND intentionally remains: PostgreSQL cannot safely remove
    # an enum label without a destructive type rewrite.
