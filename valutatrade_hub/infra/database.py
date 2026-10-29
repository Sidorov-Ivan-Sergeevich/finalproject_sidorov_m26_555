"""JSON storage with singleton access and atomic file replacement."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any, ClassVar, Self

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.infra.settings import SettingsLoader

DEFAULT_DOCUMENTS: dict[str, Any] = {
    "users": [],
    "portfolios": [],
    "rates": {
        "pairs": {},
        "last_refresh": None,
    },
    "history": [],
}


class DatabaseManager:
    """Manage application JSON documents through one process-local instance."""

    _instance: ClassVar[DatabaseManager | None] = None

    def __new__(cls, settings: SettingsLoader | None = None) -> Self:
        """Use __new__ to keep the singleton implementation explicit."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self, settings: SettingsLoader | None = None) -> None:
        """Capture storage paths once without creating files on import."""
        if hasattr(self, "_paths"):
            return

        configuration = settings if settings is not None else SettingsLoader()

        self._paths = {
            name: Path(configuration.get(f"{name}_path")) for name in DEFAULT_DOCUMENTS
        }
        self._lock = RLock()

    def initialize(self) -> None:
        """Validate existing documents and create only missing files."""
        with self._lock:
            for name, path in self._paths.items():
                if path.exists():
                    self._read(name)

            for name, path in self._paths.items():
                if not path.exists():
                    self._save(name, deepcopy(DEFAULT_DOCUMENTS[name]))

    def read(self, name: str) -> Any:
        """Read a document, reporting missing or corrupted data."""
        with self._lock:
            return self._read(name)

    def write(self, name: str, data: Any) -> None:
        """Replace a valid existing document with validated JSON data."""
        with self._lock:
            self._read(name)
            self._save(name, data)

    def update(
        self,
        name: str,
        operation: Callable[[Any], None],
    ) -> Any:
        """Read, modify in memory, and save one document under a lock."""
        with self._lock:
            data = self._read(name)
            operation(data)
            self._save(name, data)
            return deepcopy(data)

    def _get_path(self, name: str) -> Path:
        """Resolve a supported logical document name."""
        if name not in self._paths:
            raise StorageError(f"Неизвестный документ '{name}'")

        return self._paths[name]

    def _read(self, name: str) -> Any:
        """Decode JSON and validate the document's top-level structure."""
        path = self._get_path(name)

        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(
                    file,
                    parse_constant=self._reject_nonfinite,
                )
        except FileNotFoundError as error:
            raise StorageError(
                f"Файл '{path.name}' не найден. Сначала инициализируйте хранилище."
            ) from error
        except (ValueError, UnicodeError) as error:
            raise StorageError(
                f"Файл '{path.name}' содержит некорректный JSON"
            ) from error
        except OSError as error:
            raise StorageError(f"Не удалось прочитать файл '{path.name}'") from error

        self._validate_document(name, data)
        return data

    @staticmethod
    def _reject_nonfinite(value: str) -> None:
        """Reject nonstandard JSON constants such as NaN and Infinity."""
        raise ValueError(f"Недопустимое числовое значение: {value}")

    @staticmethod
    def _validate_document(name: str, data: Any) -> None:
        """Validate document containers without duplicating domain models."""
        if name == "rates":
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("pairs"), dict)
                or "last_refresh" not in data
            ):
                raise StorageError("Некорректная структура документа 'rates'")

            last_refresh = data["last_refresh"]

            if last_refresh is not None and not isinstance(last_refresh, str):
                raise StorageError("'last_refresh' должен быть строкой или null")
        elif not isinstance(data, list):
            raise StorageError(f"Документ '{name}' должен содержать список")

    def _save(self, name: str, data: Any) -> None:
        """Serialize first, then replace the target using a temporary file."""
        path = self._get_path(name)
        self._validate_document(name, data)

        try:
            content = json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise StorageError(
                f"Данные документа '{name}' нельзя сохранить в JSON"
            ) from error

        temporary_path: Path | None = None

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_path = Path(file.name)
                file.write(content)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())

            os.replace(temporary_path, path)
        except OSError as error:
            raise StorageError(f"Не удалось сохранить файл '{path.name}'") from error
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
