"""Domain models for users, wallets, and portfolios."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from re import fullmatch
from typing import Any, Self

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    InsufficientFundsError,
)
from valutatrade_hub.core.utils import validate_number

MIN_PASSWORD_LENGTH = 4
SALT_BYTES = 16


class User:
    """Application user with salted password hashing."""

    def __init__(
        self,
        user_id: int,
        username: str,
        hashed_password: str,
        salt: str,
        registration_date: datetime,
    ) -> None:
        """Initialize a user from validated stored credentials."""
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("ID пользователя должен быть положительным целым числом")

        if not isinstance(salt, str) or not salt.strip():
            raise ValueError("Соль не может быть пустой")

        if (
            not isinstance(hashed_password, str)
            or fullmatch(r"[0-9a-f]{64}", hashed_password) is None
        ):
            raise ValueError("Некорректный SHA-256 хеш пароля")

        if not isinstance(registration_date, datetime):
            raise ValueError("Дата регистрации должна быть объектом datetime")

        self._user_id = user_id
        self.username = username
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = registration_date

    @property
    def user_id(self) -> int:
        """Return the immutable user identifier."""
        return self._user_id

    @property
    def username(self) -> str:
        """Return the username."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        """Set a nonempty username without surrounding whitespace."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым")

        self._username = value.strip()

    @property
    def hashed_password(self) -> str:
        """Return the stored password hash."""
        return self._hashed_password

    @property
    def salt(self) -> str:
        """Return the user-specific password salt."""
        return self._salt

    @property
    def registration_date(self) -> datetime:
        """Return the registration date."""
        return self._registration_date

    @staticmethod
    def _validate_password(password: str) -> None:
        """Require a string password of the minimum allowed length."""
        if not isinstance(password, str):
            raise ValueError("Пароль должен быть строкой")

        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"
            )

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        """Calculate the educational SHA-256 password hash."""
        return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()

    @classmethod
    def create(
        cls,
        user_id: int,
        username: str,
        password: str,
    ) -> Self:
        """Create a new user with a random salt and UTC registration date."""
        cls._validate_password(password)
        salt = secrets.token_hex(SALT_BYTES)
        hashed_password = cls._hash_password(password, salt)

        return cls(
            user_id=user_id,
            username=username,
            hashed_password=hashed_password,
            salt=salt,
            registration_date=datetime.now(UTC),
        )

    def get_user_info(self) -> dict[str, Any]:
        """Return public user information without password credentials."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": self.registration_date.isoformat(),
        }

    def change_password(self, new_password: str) -> None:
        """Validate the new password and replace its hash and salt."""
        self._validate_password(new_password)

        new_salt = secrets.token_hex(SALT_BYTES)
        new_hash = self._hash_password(new_password, new_salt)

        self._salt = new_salt
        self._hashed_password = new_hash

    def verify_password(self, password: str) -> bool:
        """Check a supplied password without storing its plaintext."""
        if not isinstance(password, str):
            return False

        candidate_hash = self._hash_password(password, self.salt)
        return hmac.compare_digest(self.hashed_password, candidate_hash)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the user and credentials for internal JSON storage."""
        return {
            **self.get_user_info(),
            "hashed_password": self.hashed_password,
            "salt": self.salt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Restore a user from a stored JSON-compatible record."""
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            hashed_password=data["hashed_password"],
            salt=data["salt"],
            registration_date=datetime.fromisoformat(data["registration_date"]),
        )


class Wallet:
    """Wallet holding a nonnegative balance in one supported currency."""

    def __init__(
        self,
        currency_code: str,
        balance: float = 0.0,
    ) -> None:
        """Initialize a wallet with a validated currency and balance."""
        self._currency_code = get_currency(currency_code).code
        self.balance = balance

    @property
    def currency_code(self) -> str:
        """Return the currency code without allowing reassignment."""
        return self._currency_code

    @property
    def balance(self) -> float:
        """Return the current balance."""
        return self._balance

    @balance.setter
    def balance(self, value: float) -> None:
        """Set a finite nonnegative numeric balance."""
        self._balance = validate_number(
            value,
            field_name="Баланс",
            allow_zero=True,
        )

    def deposit(self, amount: float) -> None:
        """Increase the balance by a finite positive amount."""
        validated_amount = validate_number(
            amount,
            field_name="amount",
        )
        self.balance = self.balance + validated_amount

    def withdraw(self, amount: float) -> None:
        """Withdraw funds or raise an error without changing the balance."""
        validated_amount = validate_number(
            amount,
            field_name="amount",
        )

        if validated_amount > self.balance:
            raise InsufficientFundsError(
                available=self.balance,
                required=validated_amount,
                code=self.currency_code,
            )

        self.balance = self.balance - validated_amount

    def get_balance_info(self) -> str:
        """Return the currency code and balance for display."""
        return f"{self.currency_code}: {self.balance:.8f}"

    def to_dict(self) -> dict[str, str | float]:
        """Serialize the wallet into JSON-compatible data."""
        return {
            "currency_code": self.currency_code,
            "balance": self.balance,
        }


class Portfolio:
    """Manage a user's wallets and calculate their total value."""

    def __init__(
        self,
        user: User,
        wallets: dict[str, Wallet] | None = None,
        *,
        rate_provider: Callable[[str, str], float] | None = None,
    ) -> None:
        """Initialize a portfolio with its owner and optional rate provider."""
        if not isinstance(user, User):
            raise ValueError("Владелец портфеля должен быть объектом User")

        if wallets is not None and not isinstance(wallets, dict):
            raise ValueError("Кошельки должны быть переданы словарём")

        self._user = user
        self._user_id = user.user_id
        self._wallets: dict[str, Wallet] = {}
        self._rate_provider = rate_provider

        if wallets is not None:
            for code, wallet in wallets.items():
                normalized_code = get_currency(code).code

                if not isinstance(wallet, Wallet):
                    raise ValueError("Значение словаря должно быть объектом Wallet")

                if normalized_code != wallet.currency_code:
                    raise ValueError("Ключ словаря должен совпадать с валютой кошелька")

                if normalized_code in self._wallets:
                    raise ValueError(
                        f"Кошелёк '{normalized_code}' указан несколько раз"
                    )

                self._wallets[normalized_code] = wallet

    @property
    def user_id(self) -> int:
        """Return the portfolio owner's identifier."""
        return self._user_id

    @property
    def user(self) -> User:
        """Return the owner without allowing reassignment."""
        return self._user

    @property
    def wallets(self) -> dict[str, Wallet]:
        """Return a shallow copy of the wallet dictionary."""
        return self._wallets.copy()

    def add_currency(self, currency_code: str) -> Wallet:
        """Create a wallet if absent and return the existing or new wallet."""
        code = get_currency(currency_code).code

        if code not in self._wallets:
            self._wallets[code] = Wallet(code)

        return self._wallets[code]

    def get_wallet(self, currency_code: str) -> Wallet:
        """Return a wallet or report that it has not been created."""
        code = get_currency(currency_code).code

        try:
            return self._wallets[code]
        except KeyError:
            raise ValueError(
                f"У вас нет кошелька '{code}'. "
                "Он создаётся автоматически при первой покупке."
            ) from None

    def get_total_value(self, base_currency: str = "USD") -> float:
        """Calculate the portfolio value using the supplied rate provider."""
        base_code = get_currency(base_currency).code
        total = 0.0

        for code, wallet in self._wallets.items():
            if wallet.balance == 0:
                continue

            if code == base_code:
                rate = 1.0
            else:
                if self._rate_provider is None:
                    raise ApiRequestError(
                        f"Источник курсов не настроен для {code}→{base_code}"
                    )

                raw_rate = self._rate_provider(code, base_code)

                try:
                    rate = validate_number(raw_rate, field_name="Курс")
                except ValueError as error:
                    raise ApiRequestError(
                        f"Некорректный курс для {code}→{base_code}"
                    ) from error

            total = validate_number(
                total + wallet.balance * rate,
                field_name="Стоимость портфеля",
                allow_zero=True,
            )

        return total

    def to_dict(self) -> dict[str, Any]:
        """Serialize the owner identifier and all wallets for JSON storage."""
        return {
            "user_id": self.user_id,
            "wallets": {
                code: wallet.to_dict() for code, wallet in self._wallets.items()
            },
        }
