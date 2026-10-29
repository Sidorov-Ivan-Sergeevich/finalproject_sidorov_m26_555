"""Tests for JSON storage and safe document updates."""

import json
from pathlib import Path

import pytest

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture
def database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> DatabaseManager:
    """Create an isolated storage instance in a temporary directory."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.chdir(tmp_path)

    settings = SettingsLoader()
    manager = DatabaseManager(settings)
    manager.initialize()

    return manager


def users_path() -> Path:
    """Return the users file path from isolated settings."""
    return SettingsLoader().get("users_path")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("users", []),
        ("portfolios", []),
        ("rates", {"pairs": {}, "last_refresh": None}),
        ("history", []),
    ],
)
def test_initialize_creates_default_documents(
    database: DatabaseManager,
    name: str,
    expected: object,
) -> None:
    """Each supported document starts with its expected structure."""
    assert database.read(name) == expected


def test_singleton_returns_same_instance(database: DatabaseManager) -> None:
    """Repeated construction returns the initialized storage instance."""
    assert DatabaseManager() is database


def test_initialize_preserves_existing_data(database: DatabaseManager) -> None:
    """Repeated initialization does not reset stored records."""
    database.write("users", [{"user_id": 1, "username": "alice"}])

    database.initialize()

    assert database.read("users") == [{"user_id": 1, "username": "alice"}]


def test_read_returns_independent_data(database: DatabaseManager) -> None:
    """Changing a loaded document does not silently modify the file."""
    loaded = database.read("users")
    loaded.append({"user_id": 1})

    assert database.read("users") == []


def test_write_preserves_unicode(database: DatabaseManager) -> None:
    """Unicode strings survive JSON serialization."""
    database.write("users", [{"username": "Иван"}])

    assert database.read("users") == [{"username": "Иван"}]
    assert "Иван" in users_path().read_text(encoding="utf-8")


def test_update_saves_modified_document(database: DatabaseManager) -> None:
    """The update callback modifies and saves one document."""
    result = database.update(
        "users",
        lambda records: records.append({"user_id": 1}),
    )

    assert result == [{"user_id": 1}]
    assert database.read("users") == [{"user_id": 1}]


def test_failed_update_preserves_file(database: DatabaseManager) -> None:
    """An exception in the callback prevents saving partial changes."""
    original = users_path().read_bytes()

    def failing_operation(records: list) -> None:
        records.append({"user_id": 1})
        raise ValueError("Test failure")

    with pytest.raises(ValueError, match="Test failure"):
        database.update("users", failing_operation)

    assert users_path().read_bytes() == original


@pytest.mark.parametrize(
    ("name", "invalid_data"),
    [
        ("users", {}),
        ("portfolios", {}),
        ("history", {}),
        ("rates", []),
    ],
)
def test_wrong_document_type_is_rejected(
    database: DatabaseManager,
    name: str,
    invalid_data: object,
) -> None:
    """Incorrect top-level containers cannot replace valid documents."""
    original = database.read(name)

    with pytest.raises(StorageError):
        database.write(name, invalid_data)

    assert database.read(name) == original


def test_unknown_document_is_rejected(database: DatabaseManager) -> None:
    """Callers cannot access arbitrary files through document names."""
    with pytest.raises(StorageError, match="Неизвестный документ"):
        database.read("../secrets")


@pytest.mark.parametrize("content", ["", "{broken", '[{"value": NaN}]'])
def test_corrupted_json_is_reported(
    database: DatabaseManager,
    content: str,
) -> None:
    """Empty, malformed, and nonstandard JSON produce storage errors."""
    users_path().write_text(content, encoding="utf-8")

    with pytest.raises(StorageError, match="некорректный JSON"):
        database.read("users")


def test_initialize_does_not_reset_corrupted_file(
    database: DatabaseManager,
) -> None:
    """Initialization reports corruption instead of replacing the file."""
    users_path().write_text("{broken", encoding="utf-8")

    with pytest.raises(StorageError):
        database.initialize()

    assert users_path().read_text(encoding="utf-8") == "{broken"


def test_write_does_not_overwrite_corrupted_file(
    database: DatabaseManager,
) -> None:
    """Ordinary writes cannot conceal corruption in an existing document."""
    users_path().write_text("{broken", encoding="utf-8")

    with pytest.raises(StorageError):
        database.write("users", [])

    assert users_path().read_text(encoding="utf-8") == "{broken"


def test_unserializable_data_preserves_file(
    database: DatabaseManager,
) -> None:
    """Serialization failure leaves the original document untouched."""
    original = users_path().read_bytes()

    with pytest.raises(StorageError):
        database.write("users", [{"invalid": {1, 2}}])

    assert users_path().read_bytes() == original


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_numbers_cannot_be_saved(
    database: DatabaseManager,
    value: float,
) -> None:
    """Nonfinite numbers are rejected before replacing the file."""
    original = users_path().read_bytes()

    with pytest.raises(StorageError):
        database.write("users", [{"value": value}])

    assert users_path().read_bytes() == original


def test_replace_failure_preserves_original_file(
    database: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed replacement keeps the original file and removes the temp file."""
    original = users_path().read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("Simulated replacement failure")

    monkeypatch.setattr(
        "valutatrade_hub.infra.database.os.replace",
        fail_replace,
    )

    with pytest.raises(StorageError, match="Не удалось сохранить"):
        database.write("users", [{"user_id": 1}])

    assert users_path().read_bytes() == original
    assert list(users_path().parent.glob("*.tmp")) == []


def test_saved_document_is_valid_json(database: DatabaseManager) -> None:
    """The resulting file can be read by the standard JSON decoder."""
    database.write("users", [{"user_id": 1}])

    with users_path().open(encoding="utf-8") as file:
        assert json.load(file) == [{"user_id": 1}]
