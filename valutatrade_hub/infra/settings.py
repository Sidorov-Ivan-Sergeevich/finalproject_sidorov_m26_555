"""Application configuration loaded from pyproject.toml."""

from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar, Self

from valutatrade_hub.core.currencies import get_currency

DEFAULT_SETTINGS: dict[str, Any] = {
    "data_dir": "data",
    "logs_dir": "logs",
    "rates_ttl_seconds": 300,
    "default_base_currency": "USD",
    "log_level": "INFO",
    "log_format": "json",
    "log_max_bytes": 1_048_576,
    "log_backup_count": 3,
}

DATA_FILENAMES = {
    "users_path": "users.json",
    "portfolios_path": "portfolios.json",
    "rates_path": "rates.json",
    "history_path": "exchange_rates.json",
}


class SettingsLoader:
    """Load and cache configuration in one instance per Python process."""

    _instance: ClassVar[SettingsLoader | None] = None

    def __new__(cls, config_path: str | Path | None = None) -> Self:
        """Use __new__ for a simple, explicit singleton implementation."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load settings once, rejecting a different configuration path."""
        if hasattr(self, "_settings"):
            if (
                config_path is not None
                and Path(config_path).resolve() != self._config_path
            ):
                raise ValueError(
                    "SettingsLoader уже настроен на другой файл конфигурации"
                )
            return

        self._config_path = Path(
            config_path if config_path is not None else "pyproject.toml"
        ).resolve()
        self._require_config = config_path is not None
        self.reload()

    def get(self, key: str, default: Any = None) -> Any:
        """Return a setting without exposing mutable internal data."""
        return deepcopy(self._settings.get(key, default))

    def reload(self) -> None:
        """Reload settings, preserving previous values if validation fails."""
        overrides = self._read_config()
        settings = deepcopy(DEFAULT_SETTINGS)
        settings.update(overrides)

        self._validate_settings(settings)

        for key in ("data_dir", "logs_dir"):
            directory = Path(settings[key])

            if not directory.is_absolute():
                directory = self._config_path.parent / directory

            settings[key] = directory.resolve()

        for key, filename in DATA_FILENAMES.items():
            settings[key] = settings["data_dir"] / filename

        settings["actions_log_path"] = settings["logs_dir"] / "actions.log"
        settings["parser_log_path"] = settings["logs_dir"] / "parser.log"

        self._settings = settings

    def _read_config(self) -> dict[str, Any]:
        """Read the optional tool.valutatrade table from a TOML file."""
        if not self._config_path.exists() and not self._require_config:
            return {}

        try:
            with self._config_path.open("rb") as file:
                document = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(
                f"Не удалось прочитать конфигурацию: {self._config_path}"
            ) from error

        tool_section = document.get("tool", {})

        if not isinstance(tool_section, dict):
            raise ValueError("Секция [tool] должна быть таблицей")

        settings_section = tool_section.get("valutatrade", {})

        if not isinstance(settings_section, dict):
            raise ValueError("Секция [tool.valutatrade] должна быть таблицей")

        return settings_section

    @staticmethod
    def _validate_settings(settings: dict[str, Any]) -> None:
        """Validate paths, cache policy, base currency, and logging options."""
        for key in ("data_dir", "logs_dir"):
            value = settings[key]

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Настройка '{key}' должна быть непустой строкой")

        for key in (
            "rates_ttl_seconds",
            "log_max_bytes",
            "log_backup_count",
        ):
            value = settings[key]

            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(
                    f"Настройка '{key}' должна быть положительным целым числом"
                )

        settings["default_base_currency"] = get_currency(
            settings["default_base_currency"]
        ).code

        if settings["log_level"] not in (
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        ):
            raise ValueError("Некорректный уровень логирования")

        if settings["log_format"] not in ("json", "text"):
            raise ValueError("Формат логов должен быть 'json' или 'text'")
