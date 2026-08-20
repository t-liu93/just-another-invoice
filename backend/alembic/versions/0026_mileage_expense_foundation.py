"""M11 mileage expense foundation.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-20

This additive migration keeps all existing expense values untouched while
backfilling their new ``kind`` to PURCHASE.  It creates editable transport and
rate dictionaries, trip/audit tables, and idempotently seeds each existing
company with the M11 defaults.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE expense
            ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'PURCHASE',
            ADD CONSTRAINT chk_expense_kind CHECK (kind IN ('PURCHASE', 'MILEAGE'))
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_expense_company_kind_date "
        "ON expense (company_id, kind, expense_date)"
    )

    op.execute(
        """
        CREATE TABLE mileage_transport_type (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL,
            name TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_mileage_transport_type_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT uq_mileage_transport_type_company_name UNIQUE (company_id, name),
            CONSTRAINT chk_mileage_transport_type_name_nonblank CHECK (btrim(name) <> '')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_mileage_transport_type_company_id ON mileage_transport_type (company_id)"
    )

    op.execute(
        """
        CREATE TABLE mileage_rate (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL,
            transport_type_id UUID NULL,
            effective_from DATE NOT NULL,
            rate_per_km NUMERIC(18,3) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_mileage_rate_company
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_mileage_rate_transport_type
                FOREIGN KEY (transport_type_id)
                REFERENCES mileage_transport_type(id) ON DELETE CASCADE,
            CONSTRAINT chk_mileage_rate_positive CHECK (rate_per_km > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_mileage_rate_company_id ON mileage_rate (company_id)")
    op.execute("CREATE INDEX ix_mileage_rate_transport_type_id ON mileage_rate (transport_type_id)")
    op.execute("CREATE INDEX ix_mileage_rate_effective_from ON mileage_rate (effective_from)")
    op.execute(
        "CREATE UNIQUE INDEX uq_mileage_rate_general_effective "
        "ON mileage_rate (company_id, effective_from) WHERE transport_type_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_mileage_rate_type_effective "
        "ON mileage_rate (company_id, transport_type_id, effective_from) "
        "WHERE transport_type_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE mileage_trip (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL,
            expense_id UUID NULL UNIQUE,
            ownership TEXT NOT NULL DEFAULT 'PRIVATE',
            transport_type_id UUID NULL,
            transport_type_name TEXT NOT NULL,
            rate_rule_id UUID NULL,
            rate_effective_from DATE NOT NULL,
            rate_per_km NUMERIC(18,3) NOT NULL,
            trip_date DATE NOT NULL,
            one_way_distance_km NUMERIC(18,3) NOT NULL,
            total_distance_km NUMERIC(18,3) NOT NULL,
            round_trip BOOLEAN NOT NULL DEFAULT false,
            calculated_amount NUMERIC(18,3) NOT NULL,
            origin_address TEXT NULL,
            destination_address TEXT NULL,
            purpose TEXT NULL,
            note TEXT NULL,
            creator_id UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_mileage_trip_company FOREIGN KEY (company_id)
                REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_mileage_trip_expense FOREIGN KEY (expense_id)
                REFERENCES expense(id) ON DELETE CASCADE,
            CONSTRAINT fk_mileage_trip_transport_type FOREIGN KEY (transport_type_id)
                REFERENCES mileage_transport_type(id) ON DELETE SET NULL,
            CONSTRAINT fk_mileage_trip_rate_rule FOREIGN KEY (rate_rule_id)
                REFERENCES mileage_rate(id) ON DELETE SET NULL,
            CONSTRAINT fk_mileage_trip_creator FOREIGN KEY (creator_id)
                REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT chk_mileage_trip_ownership CHECK (ownership = 'PRIVATE'),
            CONSTRAINT chk_mileage_trip_one_way_positive CHECK (one_way_distance_km > 0),
            CONSTRAINT chk_mileage_trip_total_positive CHECK (total_distance_km > 0),
            CONSTRAINT chk_mileage_trip_rate_positive CHECK (rate_per_km > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_mileage_trip_company_id ON mileage_trip (company_id)")
    op.execute("CREATE INDEX ix_mileage_trip_trip_date ON mileage_trip (trip_date)")

    op.execute(
        """
        CREATE TABLE mileage_rate_adjustment (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL,
            trip_id UUID NOT NULL,
            old_rate_rule_id UUID NULL,
            new_rate_rule_id UUID NULL,
            old_rate_transport_type_id UUID NULL,
            new_rate_transport_type_id UUID NULL,
            old_rate_transport_type_name TEXT NULL,
            new_rate_transport_type_name TEXT NULL,
            old_rate_effective_from DATE NOT NULL,
            new_rate_effective_from DATE NOT NULL,
            old_rate_per_km NUMERIC(18,3) NOT NULL,
            new_rate_per_km NUMERIC(18,3) NOT NULL,
            old_amount NUMERIC(18,3) NOT NULL,
            new_amount NUMERIC(18,3) NOT NULL,
            actor_id UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id),
            CONSTRAINT fk_mileage_rate_adjustment_company FOREIGN KEY (company_id)
                REFERENCES company(id) ON DELETE RESTRICT,
            CONSTRAINT fk_mileage_rate_adjustment_trip FOREIGN KEY (trip_id)
                REFERENCES mileage_trip(id) ON DELETE CASCADE,
            CONSTRAINT fk_mileage_rate_adjustment_actor FOREIGN KEY (actor_id)
                REFERENCES "user"(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_mileage_rate_adjustment_company_id ON mileage_rate_adjustment (company_id)"
    )
    op.execute(
        "CREATE INDEX ix_mileage_rate_adjustment_trip_id ON mileage_rate_adjustment (trip_id)"
    )

    # Existing companies: editable category, four types, and general schedules.
    #
    # Reusing a name/code is safe only when that row has the frozen mileage
    # semantics.  Do not silently point a new default at an inactive category
    # or reinterpret a user-maintained VAT treatment.  Raising here keeps the
    # migration transactional and gives an operator an actionable resolution
    # before retrying the upgrade.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM expense_category
                WHERE name = 'Mileage'
                  AND (active IS NOT TRUE OR default_deductible IS DISTINCT FROM false)
            ) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'M11 migration conflict: existing Mileage category must be active ' ||
                    'and non-deductible; resolve it before retrying';
            END IF;

            IF EXISTS (
                SELECT 1 FROM mileage_transport_type
                WHERE name IN ('Car', 'Motorcycle', 'Bicycle', 'Other')
                  AND active IS NOT TRUE
            ) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'M11 migration conflict: existing seeded transport type is inactive; ' ||
                    'reactivate it before retrying';
            END IF;

            IF EXISTS (
                SELECT 1 FROM vat_treatment
                WHERE code = 'NL_PRIVATE_TRANSPORT_MILEAGE'
                  AND (
                      side <> 'PURCHASE'
                      OR effect <> 'EXEMPT'
                      OR report_box IS NOT NULL
                      OR requires_icp IS NOT FALSE
                      OR deductible IS DISTINCT FROM false
                      OR active IS NOT TRUE
                  )
            ) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'M11 migration conflict: existing NL_PRIVATE_TRANSPORT_MILEAGE ' ||
                    'VAT treatment has incompatible semantics; resolve it before retrying';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        INSERT INTO expense_category (company_id, name, default_deductible, active)
        SELECT id, 'Mileage', false, true FROM company
        ON CONFLICT (company_id, name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO mileage_transport_type (company_id, name, active)
        SELECT c.id, v.name, true FROM company c
        CROSS JOIN (VALUES ('Car'), ('Motorcycle'), ('Bicycle'), ('Other')) AS v(name)
        ON CONFLICT (company_id, name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO mileage_rate (company_id, transport_type_id, effective_from, rate_per_km)
        SELECT c.id, NULL, v.effective_from, v.rate_per_km FROM company c
        CROSS JOIN (VALUES (DATE '2024-01-01', 0.230), (DATE '2026-01-01', 0.250))
            AS v(effective_from, rate_per_km)
        ON CONFLICT (company_id, effective_from) WHERE transport_type_id IS NULL DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO vat_treatment
            (company_id, code, label, side, effect, report_box, requires_icp, deductible, active)
        SELECT id, 'NL_PRIVATE_TRANSPORT_MILEAGE', 'NL Private Transport Mileage',
               'PURCHASE', 'EXEMPT', NULL, false, false, true
        FROM company
        ON CONFLICT (company_id, code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO setting (id, level, scope_id, key, value)
        SELECT gen_random_uuid(), 'COMPANY', c.id, 'expense.mileage.defaults',
               jsonb_build_object('expense_category_id', ec.id, 'default_transport_type_id', tt.id)
        FROM company c
        JOIN expense_category ec ON ec.company_id = c.id AND ec.name = 'Mileage'
        JOIN mileage_transport_type tt ON tt.company_id = c.id AND tt.name = 'Car'
        ON CONFLICT (level, scope_id, key) DO NOTHING
        """
    )


def downgrade() -> None:
    # The typed M11 setting points at M11-only transport type UUIDs.  Remove
    # it before dropping those rows so a later re-upgrade creates live defaults
    # instead of preserving a dangling reference through ON CONFLICT DO NOTHING.
    op.execute("DELETE FROM setting WHERE key = 'expense.mileage.defaults'")
    op.execute("DROP TABLE IF EXISTS mileage_rate_adjustment")
    op.execute("DROP TABLE IF EXISTS mileage_trip")
    op.execute("DROP TABLE IF EXISTS mileage_rate")
    op.execute("DROP TABLE IF EXISTS mileage_transport_type")
    op.execute("DROP INDEX IF EXISTS ix_expense_company_kind_date")
    op.execute("ALTER TABLE expense DROP CONSTRAINT IF EXISTS chk_expense_kind")
    op.execute("ALTER TABLE expense DROP COLUMN IF EXISTS kind")
