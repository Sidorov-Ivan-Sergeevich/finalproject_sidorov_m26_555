"""Tests for the Wallet domain model."""

import json

import pytest

from valutatrade_hub.core.exceptions import (
    CurrencyNotFoundError,
    InsufficientFundsError,
)
from valutatrade_hub.core.models import Wallet


def test_default_balance_is_zero() -> None:
    """A new wallet starts with an empty balance."""
    wallet = Wallet("USD")

    assert wallet.currency_code == "USD"
    assert wallet.balance == 0.0


def test_currency_code_is_normalized() -> None:
    """Wallet construction normalizes a supported currency code."""
    wallet = Wallet(" btc ", balance=0.05)

    assert wallet.currency_code == "BTC"
    assert wallet.balance == pytest.approx(0.05)


def test_unknown_currency_is_rejected() -> None:
    """An unsupported currency raises the domain exception."""
    with pytest.raises(CurrencyNotFoundError):
        Wallet("ABC")


def test_deposit_increases_balance() -> None:
    """Depositing funds adds the requested amount."""
    wallet = Wallet("USD", balance=100)

    wallet.deposit(50)

    assert wallet.balance == 150.0


def test_withdraw_decreases_balance() -> None:
    """Withdrawing funds subtracts the requested amount."""
    wallet = Wallet("USD", balance=100)

    wallet.withdraw(40)

    assert wallet.balance == 60.0


def test_withdraw_entire_balance() -> None:
    """Withdrawing all available funds leaves a zero balance."""
    wallet = Wallet("BTC", balance=0.05)

    wallet.withdraw(0.05)

    assert wallet.balance == 0.0


def test_insufficient_funds_preserves_balance() -> None:
    """An unsuccessful withdrawal does not alter the wallet."""
    wallet = Wallet("BTC", balance=0.04)

    with pytest.raises(InsufficientFundsError) as error_info:
        wallet.withdraw(0.05)

    assert wallet.balance == pytest.approx(0.04)
    assert error_info.value.available == pytest.approx(0.04)
    assert error_info.value.required == pytest.approx(0.05)
    assert error_info.value.code == "BTC"


def test_balance_setter_accepts_zero() -> None:
    """The balance property allows a zero value."""
    wallet = Wallet("USD", balance=100)

    wallet.balance = 0

    assert wallet.balance == 0.0


@pytest.mark.parametrize(
    "value",
    [-1, "100", None, True, False, float("nan"), float("inf"), -float("inf")],
)
def test_constructor_rejects_invalid_balance(value: object) -> None:
    """Construction rejects negative, nonnumeric, and nonfinite balances."""
    with pytest.raises(ValueError):
        Wallet("USD", balance=value)


@pytest.mark.parametrize(
    "value",
    [-1, "100", None, True, False, float("nan"), float("inf"), -float("inf")],
)
def test_invalid_balance_assignment_preserves_balance(value: object) -> None:
    """A rejected balance assignment keeps the previous value."""
    wallet = Wallet("USD", balance=100)

    with pytest.raises(ValueError):
        wallet.balance = value

    assert wallet.balance == 100.0


@pytest.mark.parametrize("method_name", ["deposit", "withdraw"])
@pytest.mark.parametrize(
    "value",
    [0, -1, "100", None, True, False, float("nan"), float("inf"), -float("inf")],
)
def test_operations_reject_invalid_amounts(
    method_name: str,
    value: object,
) -> None:
    """Invalid operations leave the wallet unchanged."""
    wallet = Wallet("USD", balance=100)
    operation = getattr(wallet, method_name)

    with pytest.raises(ValueError):
        operation(value)

    assert wallet.balance == 100.0


def test_deposit_overflow_preserves_balance() -> None:
    """A deposit producing a nonfinite balance is rejected."""
    wallet = Wallet("USD", balance=1e308)

    with pytest.raises(ValueError):
        wallet.deposit(1e308)

    assert wallet.balance == 1e308


def test_currency_code_is_read_only() -> None:
    """A wallet cannot be reassigned to another currency."""
    wallet = Wallet("USD")

    with pytest.raises(AttributeError):
        wallet.currency_code = "EUR"

    assert wallet.currency_code == "USD"


def test_balance_info() -> None:
    """The display string contains the code and formatted balance."""
    wallet = Wallet("BTC", balance=0.05)

    assert wallet.get_balance_info() == "BTC: 0.05000000"


def test_wallet_serialization() -> None:
    """The serialized wallet can be encoded as JSON."""
    wallet = Wallet("USD", balance=150)

    data = json.loads(json.dumps(wallet.to_dict()))

    assert data == {
        "currency_code": "USD",
        "balance": 150.0,
    }
