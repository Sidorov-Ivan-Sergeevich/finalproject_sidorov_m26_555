"""Domain exceptions for ValutaTrade Hub."""


class ValutaTradeError(Exception):
    """Base class for application domain errors."""


class InsufficientFundsError(ValutaTradeError):
    """Raised when a wallet has insufficient funds."""

    def __init__(
        self,
        available: float,
        required: float,
        code: str,
    ) -> None:
        """Store transaction details and prepare the error message."""
        self.available = available
        self.required = required
        self.code = code

        message = (
            f"Недостаточно средств: доступно {available} {code}, "
            f"требуется {required} {code}"
        )
        super().__init__(message)


class CurrencyNotFoundError(ValutaTradeError):
    """Raised when a currency is not supported."""

    def __init__(self, code: str) -> None:
        """Store the unknown currency code."""
        self.code = code
        super().__init__(f"Неизвестная валюта '{code}'")


class ApiRequestError(ValutaTradeError):
    """Raised when an external rate provider cannot return valid data."""

    def __init__(self, reason: str) -> None:
        """Store the reason for the API failure."""
        self.reason = reason
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")


class StorageError(ValutaTradeError):
    """Raised when application data cannot be read or safely saved."""

    def __init__(self, reason: str) -> None:
        """Store the storage failure reason."""
        self.reason = reason
        super().__init__(f"Ошибка хранилища: {reason}")


class AuthenticationError(ValutaTradeError):
    """Raised when authentication fails or login is required."""


class UserAlreadyExistsError(ValutaTradeError):
    """Raised when registration uses an existing username."""

    def __init__(self, username: str) -> None:
        """Store the conflicting username and prepare the message."""
        self.username = username
        super().__init__(f"Имя пользователя '{username}' уже занято")
