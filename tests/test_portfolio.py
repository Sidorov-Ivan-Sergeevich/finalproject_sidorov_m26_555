"""Tests for the Portfolio domain model."""

import json

import pytest

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
)
from valutatrade_hub.core.models import Portfolio, User, Wallet


def fake_rate(from_code: str, to_code: str) -> float:
    """Return artificial rates used only for deterministic tests."""
    rates = {
        ("EUR", "USD"): 2.0,
        ("BTC", "USD"): 100.0,
        ("USD", "EUR"): 0.5,
        ("BTC", "EUR"): 50.0,
    }
    return rates[(from_code, to_code)]


@pytest.fixture
def user() -> User:
    """Create a portfolio owner for an individual test."""
    return User.create(1, "alice", "1234")


@pytest.fixture
def portfolio(user: User) -> Portfolio:
    """Create a portfolio with artificial balances and a fake rate source."""
    return Portfolio(
        user=user,
        wallets={
            "USD": Wallet("USD", 100),
            "EUR": Wallet("EUR", 20),
            "BTC": Wallet("BTC", 0.5),
        },
        rate_provider=fake_rate,
    )


def test_empty_portfolio(user: User) -> None:
    """A newly created portfolio contains no wallets."""
    portfolio = Portfolio(user)

    assert portfolio.user is user
    assert portfolio.user_id == user.user_id
    assert portfolio.wallets == {}
    assert portfolio.get_total_value() == 0.0


def test_owner_is_read_only(user: User) -> None:
    """The public owner property cannot be reassigned."""
    portfolio = Portfolio(user)

    with pytest.raises(AttributeError):
        portfolio.user = User.create(2, "bob", "1234")

    assert portfolio.user is user


def test_add_currency_creates_wallet(user: User) -> None:
    """Adding a currency creates an empty wallet with a normalized code."""
    portfolio = Portfolio(user)

    wallet = portfolio.add_currency(" btc ")

    assert isinstance(wallet, Wallet)
    assert wallet.currency_code == "BTC"
    assert wallet.balance == 0.0
    assert portfolio.get_wallet("BTC") is wallet


def test_add_existing_currency_preserves_balance(user: User) -> None:
    """Adding the same currency again does not replace its wallet."""
    portfolio = Portfolio(user)
    first_wallet = portfolio.add_currency("USD")
    first_wallet.deposit(100)

    second_wallet = portfolio.add_currency("usd")

    assert second_wallet is first_wallet
    assert second_wallet.balance == 100.0
    assert len(portfolio.wallets) == 1


def test_add_unknown_currency_is_rejected(user: User) -> None:
    """An unknown currency does not add a wallet."""
    portfolio = Portfolio(user)

    with pytest.raises(CurrencyNotFoundError):
        portfolio.add_currency("ABC")

    assert portfolio.wallets == {}


def test_get_missing_wallet(user: User) -> None:
    """Looking up an absent wallet gives an understandable error."""
    portfolio = Portfolio(user)

    with pytest.raises(ValueError, match="У вас нет кошелька 'BTC'"):
        portfolio.get_wallet("BTC")


def test_wallets_returns_dictionary_copy(portfolio: Portfolio) -> None:
    """Changing the returned dictionary does not remove portfolio wallets."""
    wallets_copy = portfolio.wallets
    wallets_copy.clear()

    assert len(portfolio.wallets) == 3


def test_wallet_objects_remain_accessible(portfolio: Portfolio) -> None:
    """The copied dictionary contains the actual wallet objects."""
    wallets_copy = portfolio.wallets
    wallets_copy["USD"].deposit(50)

    assert portfolio.get_wallet("USD").balance == 150.0


def test_constructor_copies_wallet_dictionary(user: User) -> None:
    """Later changes to the input dictionary do not remove stored wallets."""
    initial_wallets = {"USD": Wallet("USD", 100)}
    portfolio = Portfolio(user, initial_wallets)

    initial_wallets.clear()

    assert portfolio.get_wallet("USD").balance == 100.0


def test_total_value_in_usd(portfolio: Portfolio) -> None:
    """Artificial USD valuation equals 100 + 20 * 2 + 0.5 * 100."""
    assert portfolio.get_total_value() == pytest.approx(190.0)


def test_total_value_in_eur(portfolio: Portfolio) -> None:
    """Artificial EUR valuation equals 100 * 0.5 + 20 + 0.5 * 50."""
    assert portfolio.get_total_value(" eur ") == pytest.approx(95.0)


def test_base_currency_wallet_needs_no_rate_provider(user: User) -> None:
    """A wallet already in the base currency needs no conversion."""
    portfolio = Portfolio(user, {"USD": Wallet("USD", 100)})

    assert portfolio.get_total_value() == 100.0


def test_zero_balance_needs_no_rate_provider(user: User) -> None:
    """An empty foreign-currency wallet does not require a rate."""
    portfolio = Portfolio(user)
    portfolio.add_currency("BTC")

    assert portfolio.get_total_value() == 0.0


def test_missing_rate_provider_raises_error(user: User) -> None:
    """A nonzero foreign balance requires a configured rate source."""
    portfolio = Portfolio(user, {"BTC": Wallet("BTC", 0.5)})

    with pytest.raises(ApiRequestError, match="Источник курсов не настроен"):
        portfolio.get_total_value()


def test_unknown_base_currency_is_rejected(portfolio: Portfolio) -> None:
    """An unsupported base code raises the currency exception."""
    with pytest.raises(CurrencyNotFoundError):
        portfolio.get_total_value("ABC")


@pytest.mark.parametrize(
    "rate",
    [0, -1, True, "100", float("nan"), float("inf")],
)
def test_invalid_rate_is_rejected(user: User, rate: object) -> None:
    """Invalid provider data cannot be used for valuation."""
    portfolio = Portfolio(
        user,
        {"BTC": Wallet("BTC", 0.5)},
        rate_provider=lambda _from, _to: rate,
    )

    with pytest.raises(ApiRequestError, match="Некорректный курс"):
        portfolio.get_total_value()


def test_constructor_rejects_wrong_owner() -> None:
    """The owner must be an actual User object."""
    with pytest.raises(ValueError, match="объектом User"):
        Portfolio(user=1)


def test_constructor_rejects_mismatched_wallet_code(user: User) -> None:
    """A dictionary key must match the currency of its wallet."""
    with pytest.raises(ValueError, match="совпадать"):
        Portfolio(user, {"USD": Wallet("EUR")})


def test_constructor_rejects_duplicate_normalized_codes(user: User) -> None:
    """Different spellings cannot create two wallets for one currency."""
    with pytest.raises(ValueError, match="несколько раз"):
        Portfolio(
            user,
            {
                "USD": Wallet("USD"),
                "usd": Wallet("USD"),
            },
        )


def test_constructor_rejects_non_wallet_value(user: User) -> None:
    """Dictionary values must be Wallet objects."""
    with pytest.raises(ValueError, match="объектом Wallet"):
        Portfolio(user, {"USD": 100})


def test_portfolio_serialization(portfolio: Portfolio) -> None:
    """JSON data contains the owner identifier and serialized wallets."""
    data = json.loads(json.dumps(portfolio.to_dict()))

    assert data["user_id"] == 1
    assert data["wallets"]["USD"] == {
        "currency_code": "USD",
        "balance": 100.0,
    }
    assert len(data["wallets"]) == 3
    assert "hashed_password" not in data
    assert "salt" not in data
