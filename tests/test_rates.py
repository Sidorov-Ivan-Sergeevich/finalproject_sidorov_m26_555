"""Tests for fresh cached rates and conversion directions."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from valutatrade_hub.core.rates import RateService

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
)
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader

FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_record(
    rate: object,
    *,
    age_seconds: int = 0,
) -> dict:
    """Build an artificial cache record relative to the fixed test clock."""
    return {
        "rate": rate,
        "updated_at": (FIXED_NOW - timedelta(seconds=age_seconds)).isoformat(),
        "source": "TestSource",
    }


@pytest.fixture
def rate_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> DatabaseManager:
    """Create an isolated cache containing artificial test rates."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.chdir(tmp_path)

    database = DatabaseManager(SettingsLoader())
    database.initialize()
    database.write(
        "rates",
        {
            "pairs": {
                "EUR_USD": make_record(2.0, age_seconds=20),
                "BTC_USD": make_record(100.0, age_seconds=10),
            },
            "last_refresh": FIXED_NOW.isoformat(),
        },
    )

    return database


@pytest.fixture
def rate_service(rate_database: DatabaseManager) -> RateService:
    """Use a fixed clock so freshness tests do not depend on actual time."""
    return RateService(
        database=rate_database,
        settings=SettingsLoader(),
        clock=lambda: FIXED_NOW,
    )


def test_direct_rate(rate_service: RateService) -> None:
    """The direct pair preserves its rate and metadata."""
    quote = rate_service.get_rate("EUR", "USD")

    assert quote.rate == 2.0
    assert quote.from_code == "EUR"
    assert quote.to_code == "USD"
    assert quote.source == "TestSource"
    assert quote.updated_at == FIXED_NOW - timedelta(seconds=20)


def test_inverse_rate(rate_service: RateService) -> None:
    """An inverse pair returns the reciprocal rate."""
    quote = rate_service.get_rate("USD", "BTC")

    assert quote.rate == pytest.approx(0.01)
    assert quote.from_code == "USD"
    assert quote.to_code == "BTC"


def test_cross_rate_uses_usd(rate_service: RateService) -> None:
    """BTC to EUR equals the BTC/USD rate divided by EUR/USD."""
    quote = rate_service.get_rate("BTC", "EUR")

    assert quote.rate == pytest.approx(50.0)
    assert quote.updated_at == FIXED_NOW - timedelta(seconds=20)


def test_same_currency_needs_no_cached_pair(
    rate_service: RateService,
) -> None:
    """Converting a supported currency to itself uses the identity rate."""
    quote = rate_service.get_rate("SOL", "SOL")

    assert quote.rate == 1.0
    assert quote.source == "identity"
    assert quote.updated_at == FIXED_NOW


def test_currency_codes_are_normalized(rate_service: RateService) -> None:
    """Lookup accepts lowercase codes and surrounding whitespace."""
    quote = rate_service.get_rate(" eur ", " usd ")

    assert quote.from_code == "EUR"
    assert quote.to_code == "USD"
    assert quote.rate == 2.0


@pytest.mark.parametrize(
    ("from_code", "to_code"),
    [("ABC", "USD"), ("USD", "ABC"), ("ABC", "ABC")],
)
def test_unknown_currency_is_rejected(
    rate_service: RateService,
    from_code: str,
    to_code: str,
) -> None:
    """Unsupported codes are rejected even for identity conversions."""
    with pytest.raises(CurrencyNotFoundError):
        rate_service.get_rate(from_code, to_code)


def test_missing_pair_is_reported(rate_service: RateService) -> None:
    """A missing rate produces an instruction to update the cache."""
    with pytest.raises(ApiRequestError, match="update-rates"):
        rate_service.get_rate("SOL", "USD")


def test_rate_younger_than_ttl_is_available(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """A quote one second before expiry remains usable."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(2.0, age_seconds=299)
    rate_database.write("rates", document)

    assert rate_service.get_rate("EUR", "USD").rate == 2.0


@pytest.mark.parametrize("age_seconds", [300, 301])
def test_expired_rate_is_rejected(
    rate_service: RateService,
    rate_database: DatabaseManager,
    age_seconds: int,
) -> None:
    """A rate at or beyond the TTL boundary is not used."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(
        2.0,
        age_seconds=age_seconds,
    )
    rate_database.write("rates", document)

    with pytest.raises(ApiRequestError):
        rate_service.get_rate("EUR", "USD")


def test_future_timestamp_is_rejected(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """A future timestamp cannot make a quote valid indefinitely."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(2.0, age_seconds=-1)
    rate_database.write("rates", document)

    with pytest.raises(ApiRequestError):
        rate_service.get_rate("EUR", "USD")


def test_naive_timestamp_is_rejected(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """Cache timestamps must include a timezone."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"]["updated_at"] = "2026-01-01T12:00:00"
    rate_database.write("rates", document)

    with pytest.raises(ApiRequestError):
        rate_service.get_rate("EUR", "USD")


@pytest.mark.parametrize("value", [0, -1, True, "2", None])
def test_invalid_numeric_rate_is_rejected(
    rate_service: RateService,
    rate_database: DatabaseManager,
    value: object,
) -> None:
    """A cached rate must be a positive numeric value excluding bool."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(value)
    rate_database.write("rates", document)

    with pytest.raises(ApiRequestError):
        rate_service.get_rate("EUR", "USD")


@pytest.mark.parametrize("value", ["broken", "", None])
def test_invalid_timestamp_is_rejected(
    rate_service: RateService,
    rate_database: DatabaseManager,
    value: object,
) -> None:
    """Missing or malformed timestamps cannot pass the freshness check."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"]["updated_at"] = value
    rate_database.write("rates", document)

    with pytest.raises(ApiRequestError):
        rate_service.get_rate("EUR", "USD")


def test_fresh_inverse_can_replace_expired_direct_pair(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """An expired direct pair does not block a usable inverse pair."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(2.0, age_seconds=301)
    document["pairs"]["USD_EUR"] = make_record(0.5)
    rate_database.write("rates", document)

    assert rate_service.get_rate("EUR", "USD").rate == 2.0


def test_cross_rate_requires_both_fresh_legs(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """One expired USD leg makes the cross-rate unavailable."""
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(2.0, age_seconds=301)
    rate_database.write("rates", document)

    with pytest.raises(ApiRequestError):
        rate_service.get_rate("BTC", "EUR")


def test_numeric_adapter_returns_only_rate(
    rate_service: RateService,
) -> None:
    """Portfolio integration receives a number rather than quote metadata."""
    assert rate_service.get_rate_value("BTC", "EUR") == 50.0


def test_service_reads_updated_cache(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """The same service sees cache changes without being recreated."""
    assert rate_service.get_rate("EUR", "USD").rate == 2.0
    document = rate_database.read("rates")
    document["pairs"]["EUR_USD"] = make_record(3.0)
    rate_database.write("rates", document)

    assert rate_service.get_rate("EUR", "USD").rate == 3.0


def test_rate_lookup_does_not_modify_cache(
    rate_service: RateService,
    rate_database: DatabaseManager,
) -> None:
    """Calculated inverse and cross-rates are not written into the cache."""
    original = rate_database.read("rates")

    rate_service.get_rate("USD", "BTC")
    rate_service.get_rate("BTC", "EUR")

    assert rate_database.read("rates") == original


def test_service_uses_configured_ttl(
    rate_database: DatabaseManager,
    tmp_path: Path,
) -> None:
    """Freshness follows SettingsLoader rather than a hardcoded threshold."""
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        "[tool.valutatrade]\nrates_ttl_seconds = 5\n",
        encoding="utf-8",
    )
    settings = SettingsLoader()
    settings.reload()
    service = RateService(
        database=rate_database,
        settings=settings,
        clock=lambda: FIXED_NOW,
    )

    with pytest.raises(ApiRequestError):
        service.get_rate("BTC", "USD")
