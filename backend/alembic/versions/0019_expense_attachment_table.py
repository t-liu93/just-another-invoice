"""expense_attachment table (M8 step 2)

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-13

Adds:
- ``expense_attachment`` table
  - company_id FK RESTRICT (red-line 2)
  - expense_id FK CASCADE (red-line 3 / D12: delete expense → cascade delete rows)
  - storage_key TEXT NOT NULL (opaque key; never exposed in API)
  - filename TEXT nullable (display only; never used in storage path)
  - mime_type TEXT NOT NULL
  - byte_size INTEGER NOT NULL
  - sha256 TEXT nullable (reserved for future dedup)
  - creator_id FK SET NULL
  - timestamps
- Indexes on company_id and expense_id
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE expense_attachment (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            company_id      UUID        NOT NULL,
            expense_id      UUID        NOT NULL,

            -- Storage key: opaque, never exposed in API responses (red-line 7)
            storage_key     TEXT        NOT NULL,

            -- Display metadata (client filename stored for UX only)
            filename        TEXT,
            mime_type       TEXT        NOT NULL,
            byte_size       INTEGER     NOT NULL,

            -- Reserved for future deduplication
            sha256          TEXT,

            -- Creator (SET NULL: preserve attachment if user deleted)
            creator_id      UUID,

            -- Timestamps
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT pk_expense_attachment PRIMARY KEY (id),
            CONSTRAINT fk_expense_attachment_company
                FOREIGN KEY (company_id)
                REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_expense_attachment_expense
                FOREIGN KEY (expense_id)
                REFERENCES expense(id) ON DELETE CASCADE,
            CONSTRAINT fk_expense_attachment_creator
                FOREIGN KEY (creator_id)
                REFERENCES "user"(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_expense_attachment_company_id  ON expense_attachment (company_id)"
    )
    op.execute(
        "CREATE INDEX ix_expense_attachment_expense_id  ON expense_attachment (expense_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS expense_attachment")
