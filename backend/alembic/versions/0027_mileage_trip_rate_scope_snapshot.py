"""Persist immutable selected-rate scope on mileage trips.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-20

``mileage_rate`` rules remain editable data.  This additive migration stores
the selected rule's scope directly on each trip so future audit entries do not
mistake a subsequently edited live rule for historical state.  Existing trips
are backfilled from their still-live selected rule at upgrade time; a deleted
rule has no recoverable scope in pre-0027 data and remains NULL, matching the
existing historical-FK behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mileage_trip
            ADD COLUMN rate_transport_type_id UUID NULL,
            ADD COLUMN rate_transport_type_name TEXT NULL
        """
    )
    # Pre-snapshot trips have only the selected rule UUID.  Where that rule is
    # still live, its current scope is the best available historical fact at
    # this one-time upgrade boundary; later writes never read it back.
    op.execute(
        """
        UPDATE mileage_trip AS trip
        SET rate_transport_type_id = rate.transport_type_id,
            rate_transport_type_name = transport_type.name
        FROM mileage_rate AS rate
        LEFT JOIN mileage_transport_type AS transport_type
            ON transport_type.id = rate.transport_type_id
        WHERE trip.rate_rule_id = rate.id
          AND trip.company_id = rate.company_id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE mileage_trip
            DROP COLUMN rate_transport_type_name,
            DROP COLUMN rate_transport_type_id
        """
    )
