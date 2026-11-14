import pytest
from pydantic import ValidationError

from warehouse_scout.config import Settings

REQUIRED_ENV = {
    "POSTGRES_USER": "app",
    "POSTGRES_PASSWORD": "pg-secret",
    "POSTGRES_RO_USER": "ro",
    "POSTGRES_RO_PASSWORD": "pg-ro-secret",
    "ORACLE_USER": "tpcds",
    "ORACLE_PASSWORD": "ora-secret",
    "ORACLE_RO_USER": "ro",
    "ORACLE_RO_PASSWORD": "ora-ro-secret",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean environment holding only the required variables."""
    for name in (*REQUIRED_ENV, "ANTHROPIC_API_KEY", "ORACLE_HOST", "ORACLE_PORT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


def load() -> Settings:
    # _env_file=None: ignore any developer .env so the test sees only the fixture.
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.usefixtures("env")
def test_defaults_and_dsn() -> None:
    settings = load()
    assert settings.oracle_dsn == "localhost:1521/FREEPDB1"
    assert settings.anthropic_api_key is None


@pytest.mark.usefixtures("env")
def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORACLE_HOST", "db.internal")
    monkeypatch.setenv("ORACLE_PORT", "1599")
    assert load().oracle_dsn == "db.internal:1599/FREEPDB1"


@pytest.mark.usefixtures("env")
def test_secrets_do_not_leak_into_repr() -> None:
    settings = load()
    assert "pg-secret" not in repr(settings)
    assert settings.postgres_password.get_secret_value() == "pg-secret"


@pytest.mark.usefixtures("env")
def test_missing_required_variable_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORACLE_PASSWORD")
    with pytest.raises(ValidationError):
        load()


@pytest.mark.usefixtures("env")
def test_empty_api_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert load().anthropic_api_key is None


@pytest.mark.usefixtures("env")
def test_api_key_is_read_and_kept_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    settings = load()

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "test-key"
    assert "test-key" not in repr(settings)
