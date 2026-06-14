"""email_log table for document email send audit (M9 step 6)

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-14

New table ``email_log`` records every document email-send attempt:
- Generic ``(related_type, related_id)`` soft reference – no FK to invoice/quote
  (red-line 6: no multiple nullable FKs; audit survives document deletion).
- ``company_id`` FK → ``company.id`` ON DELETE RESTRICT (red-line 2).
- ``creator_id`` FK → ``user.id`` ON DELETE SET NULL.
- ``status`` PG enum column: SENT / FAILED.
- ``related_type`` PG enum column: INVOICE / QUOTE.
- ``error_message`` must never contain SMTP credentials (enforced at service layer).

New PG enum types created: ``emailrelatedtype``, ``emailstatus``.
Uses raw SQL for DDL (same pattern as all other migrations in this project).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE emailrelatedtype AS ENUM ('INVOICE', 'QUOTE');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE emailstatus AS ENUM ('SENT', 'FAILED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_log (
            id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
            company_id          UUID            NOT NULL,
            related_type        emailrelatedtype NOT NULL,
            related_id          UUID            NOT NULL,
            to_email            TEXT            NOT NULL,
            cc                  TEXT,
            subject             TEXT            NOT NULL,
            body_snapshot       TEXT            NOT NULL,
            attachment_filename TEXT,
            locale              VARCHAR(5),
            status              emailstatus     NOT NULL,
            error_message       TEXT,
            creator_id          UUID,
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
            sent_at             TIMESTAMPTZ,

            PRIMARY KEY (id),
            CONSTRAINT fk_email_log_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_email_log_creator
                FOREIGN KEY (creator_id) REFERENCES "user"(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN email_log.related_id IS
        'Soft reference – no FK so the audit log survives document deletion.'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_log_company_id "
        "ON email_log (company_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_log_company_related "
        "ON email_log (company_id, related_type, related_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_log_creator_id "
        "ON email_log (creator_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_email_log_creator_id")
    op.execute("DROP INDEX IF EXISTS ix_email_log_company_related")
    op.execute("DROP INDEX IF EXISTS ix_email_log_company_id")
    op.execute("DROP TABLE IF EXISTS email_log")
    op.execute("DROP TYPE IF EXISTS emailstatus")
    op.execute("DROP TYPE IF EXISTS emailrelatedtype")
