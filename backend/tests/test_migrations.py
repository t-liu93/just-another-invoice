"""Tests for Alembic migrations – upgrade / downgrade cycle.

Runs ``alembic`` in a subprocess (because ``env.py`` calls ``asyncio.run``,
which would clash with pytest-asyncio's event loop).

Marked ``@pytest.mark.integration`` — requires a running PostgreSQL instance.
Skipped by default; run with ``pytest -m integration``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from jai.config import get_settings
from jai.services.mileage import get_mileage_defaults

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_url() -> str:
    """Derive a dedicated migration-test database URL."""
    from urllib.parse import urlparse, urlunparse

    settings = get_settings()
    parsed = urlparse(settings.database_url)
    return urlunparse(parsed._replace(path="/jai_test_migrations"))


def _run_alembic(*extra_args: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *extra_args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


def _ensure_database(url: str) -> None:
    """Create the test database if it doesn't exist (via maintenance DB)."""
    import asyncio
    from urllib.parse import urlparse, urlunparse

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    maint_url = urlunparse(parsed._replace(path="/postgres"))

    async def _create() -> None:
        engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await engine.dispose()

    asyncio.run(_create())


def _drop_database(url: str) -> None:
    """Drop the test database."""
    import asyncio
    from urllib.parse import urlparse, urlunparse

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    maint_url = urlunparse(parsed._replace(path="/postgres"))

    async def _drop() -> None:
        engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await engine.dispose()

    asyncio.run(_drop())


def _query(
    url: str, statement: str, params: dict[str, object] | None = None
) -> list[dict[str, object]]:
    """Run a short SQL assertion against the isolated migration database."""

    async def _run() -> list[dict[str, object]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                rows: list[dict[str, object]] = []
                for command in (part.strip() for part in statement.split(";") if part.strip()):
                    result = await conn.execute(text(command), params or {})
                    if result.returns_rows:
                        rows = [dict(row) for row in result.mappings().all()]
                return rows
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _insert_0025_expense_sentinel(url: str) -> dict[str, uuid.UUID]:
    """Create an existing-company Expense with all M11 non-target snapshots."""
    company_id = uuid.uuid4()
    category_id = uuid.uuid4()
    rate_id = uuid.uuid4()
    treatment_id = uuid.uuid4()
    recurring_id = uuid.uuid4()
    expense_id = uuid.uuid4()
    attachment_id = uuid.uuid4()
    params: dict[str, object] = {
        "company_id": company_id,
        "category_id": category_id,
        "rate_id": rate_id,
        "treatment_id": treatment_id,
        "recurring_id": recurring_id,
        "expense_id": expense_id,
        "attachment_id": attachment_id,
    }
    _query(
        url,
        """
        INSERT INTO company (id, name, base_currency)
        VALUES (:company_id, 'Pre-M11 Co', 'EUR');
        INSERT INTO expense_category (id, company_id, name, default_deductible, active)
        VALUES (:category_id, :company_id, 'Travel', true, true);
        INSERT INTO vat_rate (id, company_id, label, percent, active)
        VALUES (:rate_id, :company_id, 'VAT 21%', 21.000, true);
        INSERT INTO vat_treatment
            (id, company_id, code, label, side, effect, report_box, requires_icp,
             deductible, active)
        VALUES
            (:treatment_id, :company_id, 'DOMESTIC', 'Domestic VAT', 'PURCHASE',
             'APPLY_RATE', '5b', false, true, true);
        INSERT INTO recurring_expense
            (id, company_id, name, category_id, category_name, vat_treatment_id,
             vat_treatment_code, vat_treatment_label, vat_treatment_effect,
             vat_rate_id, vat_rate_percent, vat_rate_label, net_amount, vat_amount,
             gross_amount, deductible, frequency, start_date, next_run_date,
             paid_by, business_percentage, depreciation_years)
        VALUES
            (:recurring_id, :company_id, 'Sentinel recurring', :category_id, 'Travel',
             :treatment_id, 'DOMESTIC', 'Domestic VAT', 'APPLY_RATE', :rate_id,
             21.000, 'VAT 21%', 10.100, 2.121, 12.221, true, 'MONTHLY',
             DATE '2025-01-01', DATE '2025-02-01', 'PRIVATE', 75.000, 3);
        INSERT INTO expense
            (id, company_id, expense_date, category_id, category_name, supplier_name,
             vat_treatment_id, vat_treatment_code, vat_treatment_label, vat_treatment_effect,
             vat_rate_id, vat_rate_percent, vat_rate_label, net_amount, vat_amount,
             gross_amount, deductible, currency, exchange_rate, base_net_amount,
             base_vat_amount, base_gross_amount, reference, note, recurring_expense_id,
             paid_by, business_percentage, depreciation_years)
        VALUES
            (:expense_id, :company_id, DATE '2025-01-15', :category_id, 'Travel',
             'Sentinel supplier', :treatment_id, 'DOMESTIC', 'Domestic VAT',
             'APPLY_RATE', :rate_id, 21.000, 'VAT 21%', 10.100, 2.121, 12.221,
             true, 'EUR', 1.23456789, 12.469, 2.619, 15.088, 'SENTINEL',
             'snapshot must survive', :recurring_id, 'PRIVATE', 75.000, 3);
        INSERT INTO expense_attachment
            (id, company_id, expense_id, storage_key, filename, mime_type, byte_size, sha256)
        VALUES (:attachment_id, :company_id, :expense_id, 'sentinel/key', 'sentinel.pdf',
                'application/pdf', 123, 'sentinel-sha');
        """,
        params,
    )
    return {key: value for key, value in params.items() if isinstance(value, uuid.UUID)}


@pytest.mark.integration
class TestMigrations:
    """Verify ``alembic upgrade head`` and ``alembic downgrade base``."""

    @classmethod
    def setup_class(cls) -> None:
        cls.url = _alembic_url()
        _ensure_database(cls.url)

    @classmethod
    def teardown_class(cls) -> None:
        _drop_database(cls.url)

    def test_upgrade_head(self) -> None:
        """``alembic upgrade head`` should succeed."""
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0, (
            f"upgrade head failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_0025_to_0026_backfills_existing_expenses_and_seeds_live_defaults(self) -> None:
        """The production upgrade preserves all pre-M11 Expense snapshots exactly."""
        assert _run_alembic("downgrade", "0025", url=self.url).returncode == 0
        ids = _insert_0025_expense_sentinel(self.url)

        result = _run_alembic("upgrade", "0026", url=self.url)
        assert result.returncode == 0, result.stderr

        expense_rows = _query(
            self.url,
            """
            SELECT kind, category_name, supplier_name, vat_treatment_code, vat_rate_percent,
                   net_amount, vat_amount, gross_amount, exchange_rate, base_gross_amount,
                   reference, note, recurring_expense_id, paid_by, business_percentage,
                   depreciation_years
            FROM expense WHERE id = :expense_id
            """,
            {"expense_id": ids["expense_id"]},
        )
        assert expense_rows == [
            {
                "kind": "PURCHASE",
                "category_name": "Travel",
                "supplier_name": "Sentinel supplier",
                "vat_treatment_code": "DOMESTIC",
                "vat_rate_percent": Decimal("21.000"),
                "net_amount": Decimal("10.100"),
                "vat_amount": Decimal("2.121"),
                "gross_amount": Decimal("12.221"),
                "exchange_rate": Decimal("1.23456789"),
                "base_gross_amount": Decimal("15.088"),
                "reference": "SENTINEL",
                "note": "snapshot must survive",
                "recurring_expense_id": ids["recurring_id"],
                "paid_by": "PRIVATE",
                "business_percentage": Decimal("75.000"),
                "depreciation_years": 3,
            }
        ]
        attachment_rows = _query(
            self.url,
            "SELECT storage_key, filename, mime_type, byte_size, sha256 "
            "FROM expense_attachment WHERE id = :attachment_id",
            {"attachment_id": ids["attachment_id"]},
        )
        assert attachment_rows == [
            {
                "storage_key": "sentinel/key",
                "filename": "sentinel.pdf",
                "mime_type": "application/pdf",
                "byte_size": 123,
                "sha256": "sentinel-sha",
            }
        ]
        seed_rows = _query(
            self.url,
            """
            SELECT
                (SELECT active FROM expense_category
                 WHERE company_id = :company_id AND name = 'Mileage') AS category_active,
                (SELECT default_deductible FROM expense_category
                 WHERE company_id = :company_id AND name = 'Mileage') AS category_deductible,
                (SELECT side::text || ':' || effect::text || ':' ||
                        COALESCE(report_box, '') || ':' ||
                        deductible::text || ':' || active::text
                 FROM vat_treatment WHERE company_id = :company_id
                   AND code = 'NL_PRIVATE_TRANSPORT_MILEAGE') AS treatment,
                (SELECT value FROM setting WHERE level = 'COMPANY' AND scope_id = :company_id
                   AND key = 'expense.mileage.defaults') AS defaults
            """,
            {"company_id": ids["company_id"]},
        )[0]
        assert _query(
            self.url,
            "SELECT name FROM mileage_transport_type WHERE company_id = :company_id ORDER BY name",
            {"company_id": ids["company_id"]},
        ) == [
            {"name": "Bicycle"},
            {"name": "Car"},
            {"name": "Motorcycle"},
            {"name": "Other"},
        ]
        assert _query(
            self.url,
            """
            SELECT effective_from, rate_per_km
            FROM mileage_rate
            WHERE company_id = :company_id AND transport_type_id IS NULL
            ORDER BY effective_from
            """,
            {"company_id": ids["company_id"]},
        ) == [
            {"effective_from": date(2024, 1, 1), "rate_per_km": Decimal("0.230")},
            {"effective_from": date(2026, 1, 1), "rate_per_km": Decimal("0.250")},
        ]
        assert seed_rows["category_active"] is True
        assert seed_rows["category_deductible"] is False
        assert seed_rows["treatment"] == "PURCHASE:EXEMPT::false:true"
        assert seed_rows["defaults"] == {
            "expense_category_id": str(
                _query(
                    self.url,
                    "SELECT id FROM expense_category WHERE company_id = :company_id "
                    "AND name = 'Mileage'",
                    {"company_id": ids["company_id"]},
                )[0]["id"]
            ),
            "default_transport_type_id": str(
                _query(
                    self.url,
                    "SELECT id FROM mileage_transport_type WHERE company_id = :company_id "
                    "AND name = 'Car'",
                    {"company_id": ids["company_id"]},
                )[0]["id"]
            ),
        }

    def test_0026_downgrade_then_reupgrade_reseeds_live_typed_defaults(self) -> None:
        """Downgrading removes M11 defaults before its referenced UUIDs vanish."""
        assert _run_alembic("downgrade", "0025", url=self.url).returncode == 0
        ids = _insert_0025_expense_sentinel(self.url)
        assert _run_alembic("upgrade", "0026", url=self.url).returncode == 0
        assert _run_alembic("downgrade", "0025", url=self.url).returncode == 0
        assert _run_alembic("upgrade", "0026", url=self.url).returncode == 0

        seeded_rows = _query(
            self.url,
            """
            SELECT
                ec.company_id = c.id AS category_is_company_local,
                ec.active AS category_active,
                tt.company_id = c.id AS type_is_company_local,
                tt.active AS type_active,
                tt.name AS type_name
            FROM company c
            JOIN setting s ON s.level = 'COMPANY' AND s.scope_id = c.id
                AND s.key = 'expense.mileage.defaults'
            JOIN expense_category ec ON ec.id = (s.value ->> 'expense_category_id')::uuid
            JOIN mileage_transport_type tt
                ON tt.id = (s.value ->> 'default_transport_type_id')::uuid
            WHERE c.id = :company_id
            """,
            {"company_id": ids["company_id"]},
        )
        assert seeded_rows == [
            {
                "category_is_company_local": True,
                "category_active": True,
                "type_is_company_local": True,
                "type_active": True,
                "type_name": "Car",
            }
        ]

        async def _assert_typed_defaults() -> None:
            engine = create_async_engine(self.url)
            try:
                async with AsyncSession(engine) as session:
                    defaults = await get_mileage_defaults(session, ids["company_id"])
                assert defaults.expense_category_id
                assert defaults.default_transport_type_id
            finally:
                await engine.dispose()

        asyncio.run(_assert_typed_defaults())

    @pytest.mark.parametrize(
        ("setup_sql", "expected_error"),
        [
            (
                "INSERT INTO expense_category (company_id, name, default_deductible, active) "
                "VALUES (:company_id, 'Mileage', false, false)",
                "existing Mileage category",
            ),
            (
                """INSERT INTO vat_treatment
                    (company_id, code, label, side, effect, report_box, requires_icp,
                     deductible, active)
                    VALUES (:company_id, 'NL_PRIVATE_TRANSPORT_MILEAGE', 'Wrong', 'SALES',
                            'APPLY_RATE', '1a', false, true, true)""",
                "existing NL_PRIVATE_TRANSPORT_MILEAGE VAT treatment",
            ),
        ],
    )
    def test_0025_to_0026_refuses_unsafe_named_seed_conflicts(
        self, setup_sql: str, expected_error: str
    ) -> None:
        assert _run_alembic("downgrade", "base", url=self.url).returncode == 0
        assert _run_alembic("upgrade", "0025", url=self.url).returncode == 0
        _query(
            self.url,
            "INSERT INTO company (id, name, base_currency) "
            "VALUES (:company_id, 'Conflict Co', 'EUR')",
            {"company_id": uuid.uuid4()},
        )
        company_id = _query(self.url, "SELECT id FROM company WHERE name = 'Conflict Co'")[0]["id"]
        _query(self.url, setup_sql, {"company_id": company_id})
        result = _run_alembic("upgrade", "0026", url=self.url)
        assert result.returncode != 0
        assert expected_error in result.stderr

    def test_0026_partial_rate_indexes_reject_general_and_type_specific_duplicates(self) -> None:
        assert _run_alembic("downgrade", "base", url=self.url).returncode == 0
        assert _run_alembic("upgrade", "0026", url=self.url).returncode == 0
        company_id = _query(self.url, "SELECT id FROM company LIMIT 1")
        if not company_id:
            _query(self.url, "INSERT INTO company (name, base_currency) VALUES ('Rate Co', 'EUR')")
            company_id = _query(self.url, "SELECT id FROM company WHERE name = 'Rate Co'")
        company = company_id[0]["id"]
        _query(
            self.url,
            """
            INSERT INTO mileage_transport_type (company_id, name, active)
            VALUES (:company_id, 'Car', true);
            INSERT INTO mileage_rate (company_id, effective_from, rate_per_km)
            VALUES (:company_id, DATE '2024-01-01', 0.230)
            """,
            {"company_id": company},
        )
        type_id = _query(
            self.url,
            "SELECT id FROM mileage_transport_type WHERE company_id = :company_id AND name = 'Car'",
            {"company_id": company},
        )[0]["id"]

        async def _assert_conflicts() -> None:
            engine = create_async_engine(self.url)
            try:
                async with engine.connect() as conn:
                    async with conn.begin():
                        with pytest.raises(IntegrityError):
                            async with conn.begin_nested():
                                await conn.execute(
                                    text(
                                        "INSERT INTO mileage_rate "
                                        "(company_id, effective_from, rate_per_km) "
                                        "VALUES (:company_id, DATE '2024-01-01', 0.999)"
                                    ),
                                    {"company_id": company},
                                )
                        await conn.execute(
                            text(
                                "INSERT INTO mileage_rate "
                                "(company_id, transport_type_id, effective_from, rate_per_km) "
                                "VALUES (:company_id, :type_id, DATE '2024-01-01', 0.999)"
                            ),
                            {"company_id": company, "type_id": type_id},
                        )
                        with pytest.raises(IntegrityError):
                            async with conn.begin_nested():
                                await conn.execute(
                                    text(
                                        "INSERT INTO mileage_rate "
                                        "(company_id, transport_type_id, "
                                        "effective_from, rate_per_km) "
                                        "VALUES (:company_id, :type_id, "
                                        "DATE '2024-01-01', 0.998)"
                                    ),
                                    {"company_id": company, "type_id": type_id},
                                )
            finally:
                await engine.dispose()

        asyncio.run(_assert_conflicts())

    def test_downgrade_base(self) -> None:
        """``alembic downgrade base`` should succeed after upgrade."""
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0
        result = _run_alembic("downgrade", "base", url=self.url)
        assert result.returncode == 0, (
            f"downgrade base failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_upgrade_then_upgrade_is_idempotent(self) -> None:
        """Running ``upgrade head`` twice should be a no-op (not error)."""
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0

    def test_downgrade_at_base_is_safe(self) -> None:
        """``downgrade base`` when already at base should not error."""
        result = _run_alembic("downgrade", "base", url=self.url)
        assert result.returncode == 0
        result = _run_alembic("downgrade", "base", url=self.url)
        assert result.returncode == 0
