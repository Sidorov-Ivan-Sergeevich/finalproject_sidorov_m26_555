"""Offline tests for the parser and the completed command interface."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from valutatrade_hub.cli.interface import CommandInterface
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import (
    AuthService,
    MarketService,
    PortfolioService,
)
from valutatrade_hub.core.utils import make_ttl_cache
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater


@pytest.fixture
def database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DatabaseManager:
    """Create isolated storage without accessing real user files."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.chdir(tmp_path)

    manager = DatabaseManager(SettingsLoader())
    manager.initialize()
    return manager


class FakeClient:
    """Return deterministic synthetic data without network access."""

    name = "coingecko"
    source = "TestSource"

    def __init__(self) -> None:
        """Prepare one fixed observation."""
        self.timestamp = datetime.now(UTC).isoformat()
        self.metadata = {
            "BTC_USD": {
                "received_at": self.timestamp,
                "source_timestamp": None,
            }
        }

    def fetch_rates(self) -> dict[str, float]:
        """Return an artificial test rate, not a market price."""
        return {"BTC_USD": 100.0}


class FailedClient:
    """Simulate an unavailable external provider."""

    name = "exchangerate"
    source = "FailedSource"

    def fetch_rates(self) -> dict[str, float]:
        """Always raise a controlled provider error."""
        raise ApiRequestError("Simulated provider failure")


def test_cache_reuses_successful_result() -> None:
    """The closure loads a value once and returns independent copies."""
    cache = make_ttl_cache(60)
    calls = []

    def load() -> list[int]:
        calls.append(1)
        return [10]

    first = cache("key", load)
    first.append(20)

    assert cache("key", load) == [10]
    assert len(calls) == 1


def test_cache_does_not_store_failures() -> None:
    """A failed loader is executed again on the next request."""
    cache = make_ttl_cache(60)
    calls = []

    def fail() -> None:
        calls.append(1)
        raise ValueError("failure")

    for _ in range(2):
        with pytest.raises(ValueError):
            cache("key", fail)

    assert len(calls) == 2


def test_parser_continues_after_one_provider_failure(
    database: DatabaseManager,
) -> None:
    """Successful quotes survive another provider's failure."""
    updater = RatesUpdater(
        [FakeClient(), FailedClient()],
        RatesStorage(database),
    )

    result = updater.run_update()

    assert result["rates_updated"] == 1
    assert len(result["errors"]) == 1
    assert database.read("rates")["pairs"]["BTC_USD"]["rate"] == 100.0


def test_repeated_observation_is_not_duplicated(
    database: DatabaseManager,
) -> None:
    """The same observation does not create duplicate history records."""
    updater = RatesUpdater(
        [FakeClient()],
        RatesStorage(database),
    )

    updater.run_update()
    result = updater.run_update()

    assert len(database.read("history")) == 1
    assert result["history_added"] == 0
    assert result["rates_updated"] == 0


def test_all_failed_providers_raise_error(
    database: DatabaseManager,
) -> None:
    """No successful data means no fabricated cache refresh."""
    updater = RatesUpdater(
        [FailedClient()],
        RatesStorage(database),
    )

    with pytest.raises(ApiRequestError):
        updater.run_update()

    assert database.read("rates")["pairs"] == {}


def test_complete_cli_flow_without_network(
    database: DatabaseManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Register, login, fund, trade, and display data through the CLI."""
    settings = SettingsLoader()
    auth = AuthService(database)
    rates = RateService(database=database, settings=settings)
    portfolios = PortfolioService(auth, database, rates)
    updater = RatesUpdater(
        [FakeClient()],
        RatesStorage(database),
    )
    market = MarketService(database, rates, updater)
    interface = CommandInterface(auth, portfolios, market)

    commands = [
        "register --username final_test --password 1234",
        "login --username final_test --password 1234",
        "deposit --amount 1000",
        "update-rates --source coingecko",
        "buy --currency BTC --amount 1",
        "sell --currency BTC --amount 0.5",
        "show-portfolio",
        "get-rate --from BTC --to USD",
        "show-rates --top 1",
    ]

    for command in commands:
        assert interface.execute(command) is True

    output = capsys.readouterr().out

    assert "Ошибка:" not in output
    assert "Покупка выполнена" in output
    assert "Продажа выполнена" in output
    assert "ИТОГО:" in output
    assert portfolios.get_portfolio().get_wallet("USD").balance == 950.0
    assert portfolios.get_portfolio().get_wallet("BTC").balance == 0.5


def test_trade_logging_contains_no_password(
    database: DatabaseManager,
) -> None:
    """Successful and failed trades produce structured safe logs."""
    settings = SettingsLoader()
    configure_logging(settings)
    auth = AuthService(database)
    auth.register("alice", "secret1234")
    auth.login("alice", "secret1234")
    rates = RateService(database=database, settings=settings)
    portfolios = PortfolioService(auth, database, rates)
    RatesUpdater(
        [FakeClient()],
        RatesStorage(database),
    ).run_update()
    portfolios.deposit_usd(1000)
    portfolios.buy("BTC", 1)

    with pytest.raises(ValueError):
        portfolios.buy("BTC", -1)

    text = settings.get("actions_log_path").read_text(encoding="utf-8")

    assert '"result": "OK"' in text
    assert '"result": "ERROR"' in text
    assert "secret1234" not in text


class FakeResponse:
    """Provide the response attributes used by the HTTP clients."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        """Keep a fake payload and HTTP status."""
        self.payload = payload
        self.status_code = status
        self.headers = {}

    def raise_for_status(self) -> None:
        """The success responses used here need no further handling."""

    def json(self) -> dict:
        """Return the supplied JSON payload."""
        return self.payload

    def close(self) -> None:
        """Match the cleanup interface of requests.Response."""


def test_fiat_client_inverts_conversion_rates(
    database: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A USD/EUR response is correctly inverted into EUR/USD."""
    config = ParserConfig(
        rates_path=SettingsLoader().get("rates_path"),
        history_path=SettingsLoader().get("history_path"),
        exchangerate_api_key="test-key",
        fiat_currencies=("EUR",),
    )
    monkeypatch.setattr(
        "valutatrade_hub.parser_service.api_clients.requests.get",
        lambda *args, **kwargs: FakeResponse(
            {
                "result": "success",
                "base_code": "USD",
                "conversion_rates": {"EUR": 0.5},
            }
        ),
    )

    assert ExchangeRateApiClient(config).fetch_rates() == {"EUR_USD": 2.0}


def test_crypto_client_uses_coin_ids(
    database: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coin identifiers map to the project's supported tickers."""
    config = ParserConfig.from_settings(SettingsLoader())
    monkeypatch.setattr(
        "valutatrade_hub.parser_service.api_clients.requests.get",
        lambda *args, **kwargs: FakeResponse(
            {
                "bitcoin": {"usd": 100},
                "ethereum": {"usd": 20},
                "solana": {"usd": 5},
            }
        ),
    )

    assert CoinGeckoClient(config).fetch_rates() == {
        "BTC_USD": 100.0,
        "ETH_USD": 20.0,
        "SOL_USD": 5.0,
    }


@pytest.mark.parametrize("status", [401, 403, 429])
def test_http_access_and_limit_errors(
    database: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """Authentication and rate-limit responses become domain errors."""
    config = ParserConfig.from_settings(SettingsLoader())
    monkeypatch.setattr(
        "valutatrade_hub.parser_service.api_clients.requests.get",
        lambda *args, **kwargs: FakeResponse({}, status=status),
    )

    with pytest.raises(ApiRequestError):
        CoinGeckoClient(config).fetch_rates()
