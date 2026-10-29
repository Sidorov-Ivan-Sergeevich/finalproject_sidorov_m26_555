"""Tests for currency models and registry lookup."""

from dataclasses import FrozenInstanceError

import pytest

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    Currency,
    FiatCurrency,
    get_currency,
)
from valutatrade_hub.core.exceptions import CurrencyNotFoundError


def test_currency_is_abstract() -> None:
    """The abstract currency cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Currency(name="US Dollar", code="USD")


def test_fiat_display_info() -> None:
    """Fiat information includes the issuing country."""
    currency = FiatCurrency(
        name="US Dollar",
        code="USD",
        issuing_country="United States",
    )

    assert currency.get_display_info() == (
        "[FIAT] USD — US Dollar (Issuing: United States)"
    )


def test_crypto_display_info() -> None:
    """Cryptocurrency information includes scientific capitalization."""
    currency = CryptoCurrency(
        name="Test Coin",
        code="TST",
        algorithm="Test algorithm",
        market_cap=1_000_000,
    )

    assert currency.get_display_info() == (
        "[CRYPTO] TST — Test Coin (Algo: Test algorithm, MCAP: 1.00e+06)"
    )


def test_unknown_market_cap_is_not_presented_as_real_data() -> None:
    """Missing capitalization is displayed as unavailable."""
    currency = CryptoCurrency(
        name="Test Coin",
        code="TST",
        algorithm="Test algorithm",
    )

    assert "MCAP: N/A" in currency.get_display_info()


@pytest.mark.parametrize("code", ["USD", "EUR", "GBP", "RUB", "BTC", "ETH", "SOL"])
def test_registry_contains_supported_currencies(code: str) -> None:
    """Every configured currency is available through the factory."""
    assert isinstance(get_currency(code), Currency)
    assert get_currency(code).code == code


def test_currency_lookup_normalizes_input() -> None:
    """Lookup accepts lowercase codes and surrounding whitespace."""
    assert get_currency(" btc ").code == "BTC"


def test_unknown_currency_raises_domain_error() -> None:
    """A well-formed unsupported code raises the domain exception."""
    with pytest.raises(CurrencyNotFoundError) as error_info:
        get_currency("ABC")

    assert error_info.value.code == "ABC"


@pytest.mark.parametrize("code", ["", "U", "USDDDD", "B TC", "US$", "usd"])
def test_currency_rejects_invalid_code(code: str) -> None:
    """Currency objects require a valid uppercase code."""
    with pytest.raises(ValueError):
        FiatCurrency(
            name="Test currency",
            code=code,
            issuing_country="Test country",
        )


@pytest.mark.parametrize("name", ["", "   ", None])
def test_currency_rejects_empty_name(name: object) -> None:
    """Names must be nonempty strings."""
    with pytest.raises(ValueError):
        FiatCurrency(
            name=name,
            code="USD",
            issuing_country="United States",
        )


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "100"])
def test_crypto_rejects_invalid_market_cap(value: object) -> None:
    """Capitalization must be finite, numeric, and nonnegative."""
    with pytest.raises(ValueError):
        CryptoCurrency(
            name="Test Coin",
            code="TST",
            algorithm="Test algorithm",
            market_cap=value,
        )


def test_currency_fields_cannot_be_reassigned() -> None:
    """A registered currency cannot be changed through field assignment."""
    currency = get_currency("USD")

    with pytest.raises(FrozenInstanceError):
        currency.code = "EUR"
