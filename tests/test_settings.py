"""Tests for configuration loading and singleton behavior."""

from pathlib import Path

import pytest

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture(autouse=True)
def isolated_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Isolate the singleton and working directory for each test."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.chdir(tmp_path)


def write_config(path: Path, content: str) -> None:
    """Write an isolated TOML configuration for a test."""
    path.write_text(content, encoding="utf-8")


def test_singleton_returns_same_instance() -> None:
    """Repeated construction returns the same settings object."""
    first = SettingsLoader()
    second = SettingsLoader()

    assert first is second


def test_missing_default_config_uses_defaults(tmp_path: Path) -> None:
    """Without a project file, default values remain available."""
    settings = SettingsLoader()

    assert settings.get("rates_ttl_seconds") == 300
    assert settings.get("default_base_currency") == "USD"
    assert settings.get("data_dir") == tmp_path / "data"


def test_config_values_override_defaults(tmp_path: Path) -> None:
    """The application table overrides selected default settings."""
    config_path = tmp_path / "custom.toml"
    write_config(
        config_path,
        """
[tool.valutatrade]
rates_ttl_seconds = 60
default_base_currency = "eur"
""",
    )

    settings = SettingsLoader(config_path)

    assert settings.get("rates_ttl_seconds") == 60
    assert settings.get("default_base_currency") == "EUR"
    assert settings.get("log_level") == "INFO"


def test_relative_paths_use_config_directory(tmp_path: Path) -> None:
    """Relative directories are resolved beside the configuration file."""
    config_dir = tmp_path / "configuration"
    config_dir.mkdir()
    config_path = config_dir / "custom.toml"
    write_config(
        config_path,
        """
[tool.valutatrade]
data_dir = "storage"
logs_dir = "journal"
""",
    )

    settings = SettingsLoader(config_path)

    assert settings.get("users_path") == config_dir / "storage" / "users.json"
    assert settings.get("rates_path") == config_dir / "storage" / "rates.json"
    assert settings.get("actions_log_path") == (config_dir / "journal" / "actions.log")


def test_get_returns_default_for_unknown_key() -> None:
    """Unknown keys use the supplied fallback value."""
    settings = SettingsLoader()

    assert settings.get("unknown") is None
    assert settings.get("unknown", "fallback") == "fallback"


def test_construction_does_not_reload_config(tmp_path: Path) -> None:
    """Repeated construction keeps the cached configuration."""
    config_path = tmp_path / "pyproject.toml"
    write_config(config_path, "[tool.valutatrade]\nrates_ttl_seconds = 60\n")
    settings = SettingsLoader()

    write_config(config_path, "[tool.valutatrade]\nrates_ttl_seconds = 120\n")

    assert SettingsLoader() is settings
    assert settings.get("rates_ttl_seconds") == 60


def test_reload_reads_updated_values(tmp_path: Path) -> None:
    """Explicit reload picks up valid configuration changes."""
    config_path = tmp_path / "pyproject.toml"
    write_config(config_path, "[tool.valutatrade]\nrates_ttl_seconds = 60\n")
    settings = SettingsLoader()

    write_config(config_path, "[tool.valutatrade]\nrates_ttl_seconds = 120\n")
    settings.reload()

    assert settings.get("rates_ttl_seconds") == 120


def test_failed_reload_preserves_previous_values(tmp_path: Path) -> None:
    """A failed reload does not replace working settings."""
    config_path = tmp_path / "pyproject.toml"
    write_config(config_path, "[tool.valutatrade]\nrates_ttl_seconds = 60\n")
    settings = SettingsLoader()

    write_config(config_path, "[tool.valutatrade]\nrates_ttl_seconds = -1\n")

    with pytest.raises(ValueError):
        settings.reload()

    assert settings.get("rates_ttl_seconds") == 60


@pytest.mark.parametrize("value", ["0", "-1", "true", '"300"'])
def test_invalid_ttl_is_rejected(tmp_path: Path, value: str) -> None:
    """TTL must be a positive integer, excluding bool and strings."""
    config_path = tmp_path / "custom.toml"
    write_config(
        config_path,
        f"[tool.valutatrade]\nrates_ttl_seconds = {value}\n",
    )

    with pytest.raises(ValueError, match="rates_ttl_seconds"):
        SettingsLoader(config_path)


def test_explicit_missing_config_is_rejected(tmp_path: Path) -> None:
    """An explicitly requested configuration file must exist."""
    with pytest.raises(ValueError, match="Не удалось прочитать"):
        SettingsLoader(tmp_path / "missing.toml")


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    """Malformed TOML produces an understandable configuration error."""
    config_path = tmp_path / "custom.toml"
    write_config(config_path, "[tool.valutatrade")

    with pytest.raises(ValueError, match="Не удалось прочитать"):
        SettingsLoader(config_path)


def test_singleton_rejects_different_config_path(tmp_path: Path) -> None:
    """An initialized singleton cannot silently switch configuration files."""
    first_path = tmp_path / "first.toml"
    write_config(first_path, "[tool.valutatrade]\n")
    SettingsLoader(first_path)

    with pytest.raises(ValueError, match="другой файл"):
        SettingsLoader(tmp_path / "second.toml")


def test_unknown_base_currency_is_rejected(tmp_path: Path) -> None:
    """The configured base currency must exist in the currency registry."""
    config_path = tmp_path / "custom.toml"
    write_config(
        config_path,
        '[tool.valutatrade]\ndefault_base_currency = "ABC"\n',
    )

    with pytest.raises(CurrencyNotFoundError):
        SettingsLoader(config_path)


def test_loading_settings_creates_no_data_directories(tmp_path: Path) -> None:
    """Reading configuration has no data-storage side effects."""
    SettingsLoader()

    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs").exists()
