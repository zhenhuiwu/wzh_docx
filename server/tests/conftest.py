"""Keep pytest off the business database."""

from __future__ import annotations

import os

import pytest

_BUSINESS_NAMES = {"docwb_dev02"}


def guard_test_database() -> str:
    url = os.environ.get("DOCWB_TEST_DATABASE_URL", "").strip()
    business = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise pytest.UsageError(
            "DOCWB_TEST_DATABASE_URL is required. Tests must not use the business DATABASE_URL."
        )
    if business and url == business:
        raise pytest.UsageError("DOCWB_TEST_DATABASE_URL must be a different database from DATABASE_URL.")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if name in _BUSINESS_NAMES:
        raise pytest.UsageError(f"Refusing to run tests against {name}.")
    os.environ["DATABASE_URL"] = url
    return url


def pytest_configure(config: pytest.Config) -> None:
    guard_test_database()


@pytest.fixture(autouse=True)
def isolated_test_storage(tmp_path, monkeypatch):
    """A migration/cleanup test must never corrupt another test's schema or files."""
    from app.config import clear_settings_cache
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "objects"))
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    clear_settings_cache()
    yield
    clear_settings_cache()
