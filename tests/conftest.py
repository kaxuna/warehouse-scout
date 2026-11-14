"""Shared pytest configuration."""

import pytest

from warehouse_scout.config import Settings, get_settings


@pytest.fixture(autouse=True)
def unit_tests_ignore_dotenv(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit tests must not read the developer's `.env`.

    CI has no `.env`. A unit test that quietly depends on one passes on a
    laptop and fails in CI. Hiding the file here makes both behave the same.
    Integration tests need the real settings and are left alone.
    """
    if request.node.get_closest_marker("integration"):
        return
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
