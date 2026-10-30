"""Application use cases for authentication and portfolio operations."""

from typing import Any

from valutatrade_hub.core.exceptions import (
    AuthenticationError,
    StorageError,
    UserAlreadyExistsError,
)
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.infra.database import DatabaseManager


class AuthService:
    """Register users and manage an in-memory authentication session."""

    def __init__(self, database: DatabaseManager | None = None) -> None:
        """Initialize storage access and start an unauthenticated session."""
        self._database = database if database is not None else DatabaseManager()
        self._current_user: User | None = None
        self._database.initialize()

    @property
    def current_user(self) -> User | None:
        """Return the authenticated user or None."""
        return self._current_user

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Validate a username and remove surrounding whitespace."""
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Имя пользователя не может быть пустым")

        return username.strip()

    def _load_users(self) -> list[User]:
        """Restore users and reject malformed or duplicate stored records."""
        records = self._database.read("users")

        try:
            users = [User.from_dict(record) for record in records]
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise StorageError(
                "Некорректные данные пользователя в users.json"
            ) from error

        identifiers = {user.user_id for user in users}
        usernames = {user.username for user in users}

        if len(identifiers) != len(users) or len(usernames) != len(users):
            raise StorageError("В users.json обнаружены повторяющиеся ID или имена")

        return users

    def _load_portfolio_records(self) -> list[dict[str, Any]]:
        """Validate portfolio identifiers and top-level wallet containers."""
        records = self._database.read("portfolios")
        identifiers: set[int] = set()

        for record in records:
            if not isinstance(record, dict):
                raise StorageError("Некорректная запись в portfolios.json")

            user_id = record.get("user_id")

            if (
                isinstance(user_id, bool)
                or not isinstance(user_id, int)
                or user_id <= 0
                or not isinstance(record.get("wallets"), dict)
            ):
                raise StorageError("Некорректная запись в portfolios.json")

            if user_id in identifiers:
                raise StorageError("В portfolios.json обнаружены повторяющиеся ID")

            identifiers.add(user_id)

        return records

    def register(self, username: str, password: str) -> User:
        """Create a user and an empty portfolio without logging them in."""
        normalized_username = self._normalize_username(username)
        users = self._load_users()

        if any(user.username == normalized_username for user in users):
            raise UserAlreadyExistsError(normalized_username)

        portfolios = self._load_portfolio_records()
        used_ids = {user.user_id for user in users}
        used_ids.update(record["user_id"] for record in portfolios)
        next_id = max(used_ids, default=0) + 1

        user = User.create(
            user_id=next_id,
            username=normalized_username,
            password=password,
        )
        portfolio = Portfolio(user)

        updated_portfolios = [*portfolios, portfolio.to_dict()]
        updated_users = [
            *(existing_user.to_dict() for existing_user in users),
            user.to_dict(),
        ]

        self._database.write("portfolios", updated_portfolios)

        try:
            self._database.write("users", updated_users)
        except StorageError:
            try:
                self._database.write("portfolios", portfolios)
            except StorageError as rollback_error:
                raise StorageError(
                    "Регистрация не завершена, откат портфеля не удался. "
                    "Проверьте users.json и portfolios.json."
                ) from rollback_error
            raise

        return user

    def login(self, username: str, password: str) -> User:
        """Authenticate a user and replace the current session on success."""
        normalized_username = self._normalize_username(username)
        users = self._load_users()
        user = next(
            (
                candidate
                for candidate in users
                if candidate.username == normalized_username
            ),
            None,
        )

        if user is None:
            raise AuthenticationError(f"Пользователь '{normalized_username}' не найден")

        if not user.verify_password(password):
            raise AuthenticationError("Неверный пароль")

        self._current_user = user
        return user

    def logout(self) -> None:
        """Clear the current authentication session."""
        self._current_user = None

    def require_login(self) -> User:
        """Return the current user or report that authentication is required."""
        if self._current_user is None:
            raise AuthenticationError("Сначала выполните login")

        return self._current_user
