"""Currency hierarchy and supported currency registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from math import isfinite
from re import fullmatch

from valutatrade_hub.core.exceptions import CurrencyNotFoundError


@dataclass(frozen=True)
class Currency(ABC):
    """Abstract currency with a validated name and uppercase code."""

    name: str
    code: str

    def __post_init__(self) -> None:
        """Validate common currency attributes."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Название валюты не может быть пустым")

        if not isinstance(self.code, str):
            raise ValueError("Код валюты должен быть строкой")

        if fullmatch(r"[A-Z0-9]{2,5}", self.code) is None:
            raise ValueError(
                "Код валюты должен содержать от 2 до 5 "
                "заглавных латинских букв или цифр"
            )

    @abstractmethod
    def get_display_info(self) -> str:
        """Return currency information for the user interface."""
        ...


@dataclass(frozen=True)
class FiatCurrency(Currency):
    """Fiat currency issued by a country or monetary union."""

    issuing_country: str

    def __post_init__(self) -> None:
        """Validate common attributes and the issuing country."""
        super().__post_init__()

        if (
            not isinstance(self.issuing_country, str)
            or not self.issuing_country.strip()
        ):
            raise ValueError("Страна или зона эмиссии не может быть пустой")

    def get_display_info(self) -> str:
        """Return the fiat currency name, code, and issuing country."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


@dataclass(frozen=True)
class CryptoCurrency(Currency):
    """Cryptocurrency with algorithm and optional capitalization data."""

    algorithm: str
    market_cap: float = 0.0

    def __post_init__(self) -> None:
        """Validate common attributes and cryptocurrency metadata."""
        super().__post_init__()

        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise ValueError("Алгоритм не может быть пустым")

        if isinstance(self.market_cap, bool) or not isinstance(
            self.market_cap, (int, float)
        ):
            raise ValueError("Капитализация должна быть числом")

        if not isfinite(self.market_cap) or self.market_cap < 0:
            raise ValueError(
                "Капитализация должна быть конечным неотрицательным числом"
            )

    def get_display_info(self) -> str:
        """Return cryptocurrency details and known capitalization."""
        market_cap_text = f"{self.market_cap:.2e}" if self.market_cap > 0 else "N/A"
        return (
            f"[CRYPTO] {self.code} — {self.name} "
            f"(Algo: {self.algorithm}, MCAP: {market_cap_text})"
        )


CURRENCY_REGISTRY: dict[str, Currency] = {
    "USD": FiatCurrency(
        name="US Dollar",
        code="USD",
        issuing_country="United States",
    ),
    "EUR": FiatCurrency(
        name="Euro",
        code="EUR",
        issuing_country="Eurozone",
    ),
    "GBP": FiatCurrency(
        name="Pound Sterling",
        code="GBP",
        issuing_country="United Kingdom",
    ),
    "RUB": FiatCurrency(
        name="Russian Ruble",
        code="RUB",
        issuing_country="Russia",
    ),
    "BTC": CryptoCurrency(
        name="Bitcoin",
        code="BTC",
        algorithm="SHA-256",
    ),
    "ETH": CryptoCurrency(
        name="Ethereum",
        code="ETH",
        algorithm="Не указан",
    ),
    "SOL": CryptoCurrency(
        name="Solana",
        code="SOL",
        algorithm="Не указан",
    ),
}


def get_currency(code: str) -> Currency:
    """Return a registered currency, normalizing the input code."""
    if not isinstance(code, str):
        raise ValueError("Код валюты должен быть строкой")

    normalized_code = code.strip().upper()

    if fullmatch(r"[A-Z0-9]{2,5}", normalized_code) is None:
        raise ValueError(
            "Код валюты должен содержать от 2 до 5 латинских букв или цифр"
        )

    try:
        return CURRENCY_REGISTRY[normalized_code]
    except KeyError:
        raise CurrencyNotFoundError(normalized_code) from None
