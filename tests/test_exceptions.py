"""Tests for application domain exceptions."""

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    ValutaTradeError,
)


def test_insufficient_funds_error() -> None:
    """The error contains balances, currency, and a readable message."""
    error = InsufficientFundsError(
        available=0.04,
        required=0.05,
        code="BTC",
    )

    assert isinstance(error, ValutaTradeError)
    assert error.available == 0.04
    assert error.required == 0.05
    assert error.code == "BTC"
    assert str(error) == ("Недостаточно средств: доступно 0.04 BTC, требуется 0.05 BTC")


def test_currency_not_found_error() -> None:
    """The error identifies the unsupported currency."""
    error = CurrencyNotFoundError("ABC")

    assert isinstance(error, ValutaTradeError)
    assert error.code == "ABC"
    assert str(error) == "Неизвестная валюта 'ABC'"


def test_api_request_error() -> None:
    """The error preserves the API failure reason."""
    error = ApiRequestError("Превышено время ожидания")

    assert isinstance(error, ValutaTradeError)
    assert error.reason == "Превышено время ожидания"
    assert str(error) == (
        "Ошибка при обращении к внешнему API: Превышено время ожидания"
    )
