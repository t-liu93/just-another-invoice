"""Tests for ``jai.config`` – Settings loading, caching, and URL assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from jai.config import Settings, get_settings


class TestDefaults:
    """Code-level defaults (checked via model_fields, not affected by .env)."""

    def test_postgres_parts(self) -> None:
        """Individual fields should have the expected code defaults."""
        f = Settings.model_fields
        assert f["postgres_host"].default == "localhost"
        assert f["postgres_port"].default == 5432
        assert f["postgres_user"].default == "jai"
        assert f["postgres_password"].default == "jai"
        assert f["postgres_db"].default == "jai"

    def test_static_dir_default_none(self) -> None:
        assert Settings.model_fields["static_dir"].default is None

    def test_app_version_default(self) -> None:
        assert Settings.model_fields["app_version"].default == "0.2.1"

    def test_database_url_default_is_none(self) -> None:
        """Code default is None (assembled by model_validator)."""
        assert Settings.model_fields["database_url"].default is None


class TestURLAssembly:
    """``database_url`` is auto-assembled from POSTGRES_* parts."""

    def test_parts_produce_correct_url(self) -> None:
        """Verify URL format assembled from default parts."""
        s = Settings(
            postgres_host="db.example.com",
            postgres_port=5432,
            postgres_user="myuser",
            postgres_password="mypass",
            postgres_db="mydb",
        )
        assert s.database_url == "postgresql+asyncpg://myuser:mypass@db.example.com:5432/mydb"

    def test_custom_port_in_url(self) -> None:
        s = Settings(postgres_port=5433)
        assert ":5433/" in (s.database_url or "")

    def test_special_chars_in_password_encoded(self) -> None:
        """Password with @:/# should be percent-encoded, not break the URL."""
        s = Settings(
            postgres_host="db.example.com",
            postgres_port=5432,
            postgres_user="myuser",
            postgres_password="p@ss:w0rd/#test",
            postgres_db="mydb",
        )
        url = s.database_url
        assert url is not None
        # The raw password must NOT appear verbatim.
        assert "p@ss:w0rd/#test" not in url
        # The encoded form should be present.
        assert "p%40ss%3Aw0rd%2F%23test" in url


class TestURLOverride:
    """``DATABASE_URL`` env var takes precedence over individual parts."""

    def test_explicit_url_wins(self) -> None:
        """If DATABASE_URL is set, individual POSTGRES_* are ignored for URL."""
        s = Settings(
            database_url="postgresql+asyncpg://custom:custom@remote:9999/customdb",
            postgres_host="should-be-ignored",
            postgres_port=1234,
        )
        assert s.database_url == "postgresql+asyncpg://custom:custom@remote:9999/customdb"

    def test_url_not_set_uses_parts(self) -> None:
        """If DATABASE_URL is None, it's assembled from parts."""
        s = Settings(
            database_url=None,
            postgres_host="mypg",
            postgres_port=5432,
        )
        assert s.database_url is not None
        assert "mypg" in s.database_url


class TestEnvFile:
    """Integration with explicit env files without relying on local repo state."""

    def test_env_file_overrides_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An env file can override the code default port (5432 -> 5433)."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_PORT", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_PORT=5433\n", encoding="utf-8")

        s = Settings(_env_file=env_file)

        assert "5433" in s.database_url

    def test_env_file_overrides_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An env file can override the code default host."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_HOST=postgres\n", encoding="utf-8")

        s = Settings(_env_file=env_file)

        assert "postgres" in (s.database_url or "")


class TestCaching:
    def test_get_settings_cached(self) -> None:
        """``get_settings()`` should return the same object on repeated calls."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


class TestExtraFields:
    def test_extra_fields_ignored(self) -> None:
        """Extra env vars should not cause validation errors (extra='ignore')."""
        s = Settings(UNKNOWN_VAR="hello")  # type: ignore[call-arg]
        assert s.database_url is not None
