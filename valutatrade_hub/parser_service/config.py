"""Centralized external rate provider configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from valutatrade_hub.infra.settings import SettingsLoader

FIAT_CURRENCIES = ("EUR", "GBP", "RUB")
CRYPTO_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


@dataclass(frozen=True)
class ParserConfig:
    """Store endpoints, credentials, currencies, and request settings."""

    rates_path: Path
    history_path: Path

    exchangerate_api_key: str | None = field(
        default_factory=lambda: os.getenv("EXCHANGERATE_API_KEY")
    )
    coingecko_api_key: str | None = field(
        default_factory=lambda: os.getenv("COINGECKO_API_KEY")
    )

    coingecko_url: str = "https://api.coingecko.com/api/v3/simple/price"
    exchangerate_url: str = "https://v6.exchangerate-api.com/v6"

    base_currency: str = "USD"
    fiat_currencies: tuple[str, ...] = FIAT_CURRENCIES
    request_timeout: float = 10.0
    response_cache_seconds: float = 30.0

    @classmethod
    def from_settings(cls, settings: SettingsLoader) -> "ParserConfig":
        """Build parser configuration using application storage paths."""
        return cls(
            rates_path=settings.get("rates_path"),
            history_path=settings.get("history_path"),
        )
