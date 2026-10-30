"""Tests for registration and authentication use cases."""

from pathlib import Path
from typing import Any

import pytest

from valutatrade_hub.core.exceptions import (
    AuthenticationError,
    StorageError,
    UserAlreadyExistsError,
)
from valutatrade_hub.core.usecases import AuthService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture
def auth_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> DatabaseManager:
    """Create storage isolated from the real application data."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.chdir(tmp_path)

    database = DatabaseManager(SettingsLoader())
    database.initialize()

    return database


@pytest.fixture
def auth_service(auth_database: DatabaseManager) -> AuthService:
    """Create a fresh session using isolated storage."""
    return AuthService(auth_database)


def test_register_saves_user_and_empty_portfolio(
    auth_service: AuthService,
    auth_database: DatabaseManager,
) -> None:
    """Registration persists credentials and creates an empty portfolio."""
    user = auth_service.register("alice", "1234")

    stored_users = auth_database.read("users")
    stored_portfolios = auth_database.read("portfolios")

    assert user.user_id == 1
    assert user.username == "alice"
    assert user.verify_password("1234")
    assert stored_users == [user.to_dict()]
    assert "password" not in stored_users[0]
    assert stored_users[0]["hashed_password"] != "1234"
    assert stored_portfolios == [{"user_id": 1, "wallets": {}}]


def test_register_does_not_log_user_in(auth_service: AuthService) -> None:
    """Registration leaves the session unauthenticated."""
    auth_service.register("alice", "1234")

    assert auth_service.current_user is None


def test_register_trims_username(auth_service: AuthService) -> None:
    """Surrounding whitespace is not part of the stored username."""
    user = auth_service.register(" alice ", "1234")

    assert user.username == "alice"


def test_duplicate_username_preserves_documents(
    auth_service: AuthService,
    auth_database: DatabaseManager,
) -> None:
    """Rejected registration does not modify either document."""
    auth_service.register("alice", "1234")
    old_users = auth_database.read("users")
    old_portfolios = auth_database.read("portfolios")

    with pytest.raises(UserAlreadyExistsError, match="уже занято"):
        auth_service.register(" alice ", "5678")

    assert auth_database.read("users") == old_users
    assert auth_database.read("portfolios") == old_portfolios


@pytest.mark.parametrize("username", ["", "   ", None])
def test_invalid_username_creates_no_records(
    auth_service: AuthService,
    auth_database: DatabaseManager,
    username: object,
) -> None:
    """Invalid usernames do not create users or portfolios."""
    with pytest.raises(ValueError):
        auth_service.register(username, "1234")

    assert auth_database.read("users") == []
    assert auth_database.read("portfolios") == []


@pytest.mark.parametrize("password", ["123", "", None, 1234])
def test_invalid_password_creates_no_records(
    auth_service: AuthService,
    auth_database: DatabaseManager,
    password: object,
) -> None:
    """Invalid passwords are rejected before saving any records."""
    with pytest.raises(ValueError):
        auth_service.register("alice", password)

    assert auth_database.read("users") == []
    assert auth_database.read("portfolios") == []


def test_registration_assigns_distinct_ids(
    auth_service: AuthService,
    auth_database: DatabaseManager,
) -> None:
    """Each registered user receives a separate identifier."""
    alice = auth_service.register("alice", "1234")
    bob = auth_service.register("bob", "5678")

    assert alice.user_id == 1
    assert bob.user_id == 2
    assert len(auth_database.read("portfolios")) == 2


def test_login_sets_current_user(auth_service: AuthService) -> None:
    """Correct credentials establish the current session."""
    registered = auth_service.register("alice", "1234")

    logged_in = auth_service.login(" alice ", "1234")

    assert logged_in.user_id == registered.user_id
    assert auth_service.current_user is logged_in
    assert auth_service.require_login() is logged_in


def test_login_rejects_unknown_user(auth_service: AuthService) -> None:
    """An unknown username produces an authentication error."""
    with pytest.raises(AuthenticationError, match="не найден"):
        auth_service.login("nobody", "1234")

    assert auth_service.current_user is None


def test_login_rejects_wrong_password(auth_service: AuthService) -> None:
    """An incorrect password does not create a session."""
    auth_service.register("alice", "1234")

    with pytest.raises(AuthenticationError, match="Неверный пароль"):
        auth_service.login("alice", "wrong")

    assert auth_service.current_user is None


def test_require_login_rejects_empty_session(
    auth_service: AuthService,
) -> None:
    """Protected operations require authentication."""
    with pytest.raises(AuthenticationError, match="Сначала выполните login"):
        auth_service.require_login()


def test_logout_clears_session(auth_service: AuthService) -> None:
    """Logout removes the authenticated user."""
    auth_service.register("alice", "1234")
    auth_service.login("alice", "1234")

    auth_service.logout()

    assert auth_service.current_user is None


def test_failed_login_preserves_existing_session(
    auth_service: AuthService,
) -> None:
    """A failed login attempt does not replace the current user."""
    auth_service.register("alice", "1234")
    logged_in = auth_service.login("alice", "1234")

    with pytest.raises(AuthenticationError):
        auth_service.login("alice", "wrong")

    assert auth_service.current_user is logged_in


def test_new_service_can_login_from_saved_data(
    auth_service: AuthService,
    auth_database: DatabaseManager,
) -> None:
    """A fresh service restores users from JSON but starts without a session."""
    auth_service.register("alice", "1234")
    another_service = AuthService(auth_database)

    assert another_service.current_user is None

    user = another_service.login("alice", "1234")

    assert user.username == "alice"


def test_invalid_stored_user_raises_storage_error(
    auth_service: AuthService,
    auth_database: DatabaseManager,
) -> None:
    """Malformed user records are not treated as missing users."""
    auth_database.write("users", [{"user_id": 1}])

    with pytest.raises(StorageError, match="Некорректные данные"):
        auth_service.login("alice", "1234")


def test_failed_user_save_rolls_back_portfolio(
    auth_service: AuthService,
    auth_database: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary user-save failure triggers portfolio rollback."""
    original_write = auth_database.write

    def fail_user_write(name: str, data: Any) -> None:
        if name == "users":
            raise StorageError("Simulated user-save failure")
        original_write(name, data)

    monkeypatch.setattr(auth_database, "write", fail_user_write)

    with pytest.raises(StorageError, match="Simulated user-save failure"):
        auth_service.register("alice", "1234")

    assert auth_database.read("users") == []
    assert auth_database.read("portfolios") == []


def test_failed_portfolio_save_creates_no_user(
    auth_service: AuthService,
    auth_database: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A portfolio-save failure prevents writing the user document."""
    original_write = auth_database.write

    def fail_portfolio_write(name: str, data: Any) -> None:
        if name == "portfolios":
            raise StorageError("Simulated portfolio-save failure")
        original_write(name, data)

    monkeypatch.setattr(auth_database, "write", fail_portfolio_write)

    with pytest.raises(StorageError, match="Simulated portfolio-save failure"):
        auth_service.register("alice", "1234")

    assert auth_database.read("users") == []
    assert auth_database.read("portfolios") == []
