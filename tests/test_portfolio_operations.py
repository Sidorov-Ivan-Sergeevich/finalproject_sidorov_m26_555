"""Tests for persisted portfolio access and virtual USD deposits."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AuthenticationError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.models import Wallet
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService, PortfolioService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture
def services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[PortfolioService, AuthService, DatabaseManager]:
    """Create isolated services with an authenticated test user."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.chdir(tmp_path)

    settings = SettingsLoader()
    database = DatabaseManager(settings)
    auth = AuthService(database)
    auth.register("alice", "1234")
    auth.login("alice", "1234")
    rates = RateService(database=database, settings=settings)
    portfolios = PortfolioService(auth, database, rates)

    return portfolios, auth, database


def test_load_empty_portfolio(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """A registered user initially owns an empty portfolio."""
    portfolios, _, _ = services

    portfolio = portfolios.get_portfolio()

    assert portfolio.user.username == "alice"
    assert portfolio.wallets == {}
    assert portfolio.get_total_value() == 0.0


@pytest.mark.parametrize("operation", ["get_portfolio", "deposit_usd"])
def test_operations_require_login(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
    operation: str,
) -> None:
    """Unauthenticated users cannot load or change a portfolio."""
    portfolios, auth, database = services
    original = database.read("portfolios")
    auth.logout()

    with pytest.raises(AuthenticationError, match="Сначала выполните login"):
        if operation == "get_portfolio":
            portfolios.get_portfolio()
        else:
            portfolios.deposit_usd(100)

    assert database.read("portfolios") == original


def test_deposit_creates_usd_wallet(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """The first deposit creates and persists a USD wallet."""
    portfolios, _, database = services

    result = portfolios.deposit_usd(100)

    assert result["currency_code"] == "USD"
    assert result["amount"] == 100.0
    assert result["balance_before"] == 0.0
    assert result["balance_after"] == 100.0
    assert database.read("portfolios")[0]["wallets"]["USD"] == {
        "currency_code": "USD",
        "balance": 100.0,
    }


def test_repeated_deposits_accumulate(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """A later deposit adds funds without resetting the wallet."""
    portfolios, _, _ = services
    portfolios.deposit_usd(100)

    result = portfolios.deposit_usd(25)

    assert result["balance_before"] == 100.0
    assert result["balance_after"] == 125.0
    assert portfolios.get_portfolio().get_wallet("USD").balance == 125.0


@pytest.mark.parametrize(
    "amount",
    [0, -1, True, "100", None, float("nan"), float("inf")],
)
def test_invalid_deposit_preserves_data(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
    amount: object,
) -> None:
    """Invalid amounts leave all stored portfolios unchanged."""
    portfolios, _, database = services
    original = database.read("portfolios")

    with pytest.raises(ValueError):
        portfolios.deposit_usd(amount)

    assert database.read("portfolios") == original


def test_loaded_portfolio_does_not_save_itself(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """Local object changes do not silently update the JSON document."""
    portfolios, _, _ = services
    portfolios.deposit_usd(100)
    loaded = portfolios.get_portfolio()

    loaded.get_wallet("USD").withdraw(20)

    assert loaded.get_wallet("USD").balance == 80.0
    assert portfolios.get_portfolio().get_wallet("USD").balance == 100.0


def test_deposit_changes_only_current_user(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """Depositing into one account does not alter another account."""
    portfolios, auth, _ = services
    portfolios.deposit_usd(100)
    auth.register("bob", "5678")
    auth.login("bob", "5678")

    portfolios.deposit_usd(40)

    assert portfolios.get_portfolio().get_wallet("USD").balance == 40.0

    auth.login("alice", "1234")

    assert portfolios.get_portfolio().get_wallet("USD").balance == 100.0


def test_restore_wallet_without_redundant_currency_code(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """A wallet may store its currency only in the dictionary key."""
    portfolios, _, database = services
    database.write(
        "portfolios",
        [{"user_id": 1, "wallets": {"USD": {"balance": 75.0}}}],
    )

    portfolio = portfolios.get_portfolio()

    assert portfolio.get_wallet("USD").balance == 75.0


@pytest.mark.parametrize(
    "wallets",
    [
        {"USD": {"balance": -1}},
        {"ABC": {"balance": 10}},
        {"USD": {"currency_code": "EUR", "balance": 10}},
        {"USD": {}},
        {"USD": 100},
    ],
)
def test_invalid_wallet_data_is_not_overwritten(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
    wallets: dict,
) -> None:
    """Malformed wallet records cause an error rather than data loss."""
    portfolios, _, database = services
    database.write(
        "portfolios",
        [{"user_id": 1, "wallets": wallets}],
    )
    original = database.read("portfolios")

    with pytest.raises(StorageError, match="Некорректные кошельки"):
        portfolios.deposit_usd(100)

    assert database.read("portfolios") == original


def test_missing_portfolio_is_not_silently_recreated(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """An absent registered user's portfolio is treated as a storage error."""
    portfolios, _, database = services
    database.write("portfolios", [])

    with pytest.raises(StorageError, match="не найден"):
        portfolios.deposit_usd(100)

    assert database.read("portfolios") == []


def test_save_failure_preserves_stored_balance(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed file replacement leaves the original balance intact."""
    portfolios, _, database = services
    portfolios.deposit_usd(100)
    original = database.read("portfolios")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("Simulated replacement failure")

    monkeypatch.setattr(
        "valutatrade_hub.infra.database.os.replace",
        fail_replace,
    )

    with pytest.raises(StorageError):
        portfolios.deposit_usd(50)

    assert database.read("portfolios") == original


@pytest.fixture
def trading_services(
    services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> tuple[PortfolioService, AuthService, DatabaseManager]:
    """Provide deterministic artificial rates for trading tests."""
    _, auth, database = services
    fixed_now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    database.write(
        "rates",
        {
            "pairs": {
                "BTC_USD": {
                    "rate": 100.0,
                    "updated_at": fixed_now.isoformat(),
                    "source": "TestSource",
                },
                "EUR_USD": {
                    "rate": 2.0,
                    "updated_at": fixed_now.isoformat(),
                    "source": "TestSource",
                },
            },
            "last_refresh": fixed_now.isoformat(),
        },
    )

    rates = RateService(
        database=database,
        settings=SettingsLoader(),
        clock=lambda: fixed_now,
    )
    portfolios = PortfolioService(auth, database, rates)

    return portfolios, auth, database


def test_buy_debits_usd_and_credits_currency(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """Buying changes both wallets and returns transaction details."""
    portfolios, _, _ = trading_services
    portfolios.deposit_usd(1000)

    result = portfolios.buy(" btc ", 0.5)

    portfolio = portfolios.get_portfolio()

    assert portfolio.get_wallet("BTC").balance == pytest.approx(0.5)
    assert portfolio.get_wallet("USD").balance == pytest.approx(950.0)
    assert result["action"] == "BUY"
    assert result["currency_code"] == "BTC"
    assert result["amount"] == 0.5
    assert result["rate"] == 100.0
    assert result["total_usd"] == 50.0
    assert result["balance_before"] == 0.0
    assert result["balance_after"] == 0.5
    assert result["usd_before"] == 1000.0
    assert result["usd_after"] == 950.0


def test_repeated_buy_preserves_previous_balance(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """A later purchase adds currency without replacing the wallet."""
    portfolios, _, _ = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)

    portfolios.buy("BTC", 0.25)

    portfolio = portfolios.get_portfolio()

    assert portfolio.get_wallet("BTC").balance == pytest.approx(0.75)
    assert portfolio.get_wallet("USD").balance == pytest.approx(925.0)


def test_sell_debits_currency_and_credits_usd(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """Selling subtracts currency and adds its USD value."""
    portfolios, _, _ = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)

    result = portfolios.sell("BTC", 0.2)

    portfolio = portfolios.get_portfolio()

    assert portfolio.get_wallet("BTC").balance == pytest.approx(0.3)
    assert portfolio.get_wallet("USD").balance == pytest.approx(970.0)
    assert result["action"] == "SELL"
    assert result["total_usd"] == pytest.approx(20.0)
    assert result["balance_before"] == pytest.approx(0.5)
    assert result["balance_after"] == pytest.approx(0.3)
    assert result["usd_before"] == pytest.approx(950.0)
    assert result["usd_after"] == pytest.approx(970.0)


def test_full_sale_restores_usd_at_unchanged_rate(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """A complete round trip has no fee at an unchanged rate."""
    portfolios, _, _ = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)

    portfolios.sell("BTC", 0.5)

    portfolio = portfolios.get_portfolio()

    assert portfolio.get_wallet("BTC").balance == 0.0
    assert portfolio.get_wallet("USD").balance == pytest.approx(1000.0)


def test_sell_creates_missing_usd_wallet(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """Selling an existing asset can create the settlement wallet."""
    portfolios, _, database = trading_services
    database.write(
        "portfolios",
        [{"user_id": 1, "wallets": {"BTC": {"balance": 1.0}}}],
    )

    portfolios.sell("BTC", 0.25)

    portfolio = portfolios.get_portfolio()

    assert portfolio.get_wallet("BTC").balance == pytest.approx(0.75)
    assert portfolio.get_wallet("USD").balance == pytest.approx(25.0)


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_trading_requires_login(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    method_name: str,
) -> None:
    """Unauthenticated trading cannot modify portfolios."""
    portfolios, auth, database = trading_services
    original = database.read("portfolios")
    auth.logout()

    with pytest.raises(AuthenticationError):
        getattr(portfolios, method_name)("BTC", 0.5)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
@pytest.mark.parametrize(
    "amount",
    [0, -1, True, "1", None, float("nan"), float("inf")],
)
def test_invalid_trade_amount_preserves_data(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    method_name: str,
    amount: object,
) -> None:
    """Invalid amounts cannot change either side of a trade."""
    portfolios, _, database = trading_services
    original = database.read("portfolios")

    with pytest.raises(ValueError):
        getattr(portfolios, method_name)("BTC", amount)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_trade_rejects_unknown_currency(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    method_name: str,
) -> None:
    """Unknown currency codes cannot create wallets or transactions."""
    portfolios, _, database = trading_services
    original = database.read("portfolios")

    with pytest.raises(CurrencyNotFoundError):
        getattr(portfolios, method_name)("ABC", 1)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_usd_is_not_traded_against_itself(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    method_name: str,
) -> None:
    """USD funding is separate from currency trading."""
    portfolios, _, database = trading_services
    original = database.read("portfolios")

    with pytest.raises(ValueError, match="пополнение"):
        getattr(portfolios, method_name)("USD", 100)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("starting_balance", [0, 10])
def test_buy_with_insufficient_usd_preserves_data(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    starting_balance: int,
) -> None:
    """An unaffordable purchase creates no persistent asset wallet."""
    portfolios, _, database = trading_services

    if starting_balance:
        portfolios.deposit_usd(starting_balance)

    original = database.read("portfolios")

    with pytest.raises(InsufficientFundsError) as error_info:
        portfolios.buy("BTC", 0.5)

    assert error_info.value.code == "USD"
    assert error_info.value.available == starting_balance
    assert error_info.value.required == 50.0
    assert database.read("portfolios") == original


def test_sell_with_insufficient_currency_preserves_data(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """An excessive sale does not increase the USD balance."""
    portfolios, _, database = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)
    original = database.read("portfolios")

    with pytest.raises(InsufficientFundsError) as error_info:
        portfolios.sell("BTC", 0.75)

    assert error_info.value.code == "BTC"
    assert error_info.value.available == 0.5
    assert error_info.value.required == 0.75
    assert database.read("portfolios") == original


def test_sell_missing_wallet_preserves_data(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
) -> None:
    """A sale requires an existing wallet for the asset."""
    portfolios, _, database = trading_services
    original = database.read("portfolios")

    with pytest.raises(ValueError, match="У вас нет кошелька 'BTC'"):
        portfolios.sell("BTC", 0.5)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_expired_rate_blocks_trading(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    method_name: str,
) -> None:
    """Expired quotes cannot be used to settle transactions."""
    portfolios, _, database = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)
    original = database.read("portfolios")
    document = database.read("rates")
    fixed_now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    document["pairs"]["BTC_USD"]["updated_at"] = (
        fixed_now - timedelta(seconds=301)
    ).isoformat()
    database.write("rates", document)

    with pytest.raises(ApiRequestError):
        getattr(portfolios, method_name)("BTC", 0.1)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_trade_save_failure_preserves_both_balances(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    """A failed replacement leaves both stored wallets unchanged."""
    portfolios, _, database = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)
    original = database.read("portfolios")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("Simulated replacement failure")

    monkeypatch.setattr(
        "valutatrade_hub.infra.database.os.replace",
        fail_replace,
    )

    with pytest.raises(StorageError):
        getattr(portfolios, method_name)("BTC", 0.1)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_credit_failure_does_not_save_debit(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    """A failure after withdrawal cannot persist half of a trade."""
    portfolios, _, database = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)
    original = database.read("portfolios")

    def fail_deposit(self: Wallet, amount: float) -> None:
        raise ValueError("Simulated credit failure")

    monkeypatch.setattr(Wallet, "deposit", fail_deposit)

    with pytest.raises(ValueError, match="Simulated credit failure"):
        getattr(portfolios, method_name)("BTC", 0.1)

    assert database.read("portfolios") == original


@pytest.mark.parametrize("method_name", ["buy", "sell"])
def test_unrepresentably_small_trade_is_rejected(
    trading_services: tuple[PortfolioService, AuthService, DatabaseManager],
    method_name: str,
) -> None:
    """A trade must actually change both balances at float precision."""
    portfolios, _, database = trading_services
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 0.5)
    original = database.read("portfolios")

    with pytest.raises(ValueError, match="Сумма слишком мала"):
        getattr(portfolios, method_name)("BTC", 1e-300)

    assert database.read("portfolios") == original
