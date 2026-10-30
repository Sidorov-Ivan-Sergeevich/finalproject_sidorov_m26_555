"""Interactive command-line interface for ValutaTrade Hub."""

import shlex
from argparse import ArgumentParser, Namespace
from typing import NoReturn

from prettytable import PrettyTable

from valutatrade_hub.core.currencies import CURRENCY_REGISTRY
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    ValutaTradeError,
)
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import (
    AuthService,
    MarketService,
    PortfolioService,
)
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.updater import build_updater


class CommandParser(ArgumentParser):
    """Parse commands without closing the interactive application."""

    def error(self, message: str) -> NoReturn:
        """Convert parsing errors into ordinary CLI-handled exceptions."""
        raise ValueError(message)


def build_parser() -> CommandParser:
    """Define all supported interactive commands."""
    parser = CommandParser(
        prog="valutatrade",
        description="Учебный виртуальный валютный кошелёк",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    help_parser = commands.add_parser(
        "help",
        help="Справка",
        allow_abbrev=False,
    )
    help_parser.add_argument("topic", nargs="?")

    for command in ("register", "login"):
        child = commands.add_parser(
            command,
            allow_abbrev=False,
        )
        child.add_argument("--username", required=True)
        child.add_argument("--password", required=True)

    for command in ("logout", "exit"):
        commands.add_parser(
            command,
            allow_abbrev=False,
        )

    deposit = commands.add_parser(
        "deposit",
        help="Пополнить виртуальный USD-кошелёк",
        allow_abbrev=False,
    )
    deposit.add_argument("--amount", type=float, required=True)

    portfolio = commands.add_parser(
        "show-portfolio",
        allow_abbrev=False,
    )
    portfolio.add_argument("--base", default="USD")

    for command in ("buy", "sell"):
        child = commands.add_parser(
            command,
            allow_abbrev=False,
        )
        child.add_argument("--currency", required=True)
        child.add_argument("--amount", type=float, required=True)

    rate = commands.add_parser(
        "get-rate",
        allow_abbrev=False,
    )
    rate.add_argument("--from", dest="from_code", required=True)
    rate.add_argument("--to", dest="to_code", required=True)

    update = commands.add_parser(
        "update-rates",
        allow_abbrev=False,
    )
    update.add_argument(
        "--source",
        choices=["coingecko", "exchangerate"],
    )

    show_rates = commands.add_parser(
        "show-rates",
        allow_abbrev=False,
    )
    show_rates.add_argument("--currency")
    show_rates.add_argument("--base", default="USD")
    show_rates.add_argument("--top", type=int)

    return parser


class CommandInterface:
    """Delegate user commands to application services."""

    def __init__(
        self,
        auth_service: AuthService,
        portfolio_service: PortfolioService,
        market_service: MarketService | None = None,
    ) -> None:
        """Keep shared services throughout the interactive session."""
        self._auth = auth_service
        self._portfolios = portfolio_service
        self._market = market_service
        self._parser = build_parser()

    def _require_market(self) -> MarketService:
        """Return the configured market service."""
        if self._market is None:
            raise ValueError("Сервис рынка не подключён")
        return self._market

    def execute(self, line: str) -> bool:
        """Execute one command and indicate whether the loop should continue."""
        try:
            tokens = shlex.split(line, posix=True)

            if not tokens:
                return True

            arguments = self._parser.parse_args(tokens)
            return self._dispatch(arguments)

        except SystemExit as error:
            if error.code not in (None, 0):
                raise
            return True
        except CurrencyNotFoundError as error:
            print(f"Ошибка: {error}")
            print("Поддерживаются: " + ", ".join(sorted(CURRENCY_REGISTRY)))
            return True
        except ApiRequestError as error:
            print(f"Ошибка: {error}")
            print("Проверьте сеть и ключи API либо повторите позже.")
            return True
        except (ValutaTradeError, ValueError) as error:
            print(f"Ошибка: {error}")
            return True

    def _dispatch(self, arguments: Namespace) -> bool:
        """Call the matching application service and render its result."""
        match arguments.command:
            case "help":
                if arguments.topic:
                    self._parser.parse_args([arguments.topic, "--help"])
                else:
                    self._parser.print_help()

            case "register":
                user = self._auth.register(
                    arguments.username,
                    arguments.password,
                )
                print(
                    f"Пользователь '{user.username}' зарегистрирован "
                    f"(id={user.user_id}). Выполните login."
                )

            case "login":
                user = self._auth.login(
                    arguments.username,
                    arguments.password,
                )
                print(f"Вы вошли как '{user.username}'")

            case "logout":
                self._auth.logout()
                print("Вы вышли из системы")

            case "deposit":
                result = self._portfolios.deposit_usd(arguments.amount)
                print(f"Пополнение выполнено: {result['amount']:.2f} USD")
                print(
                    f"Баланс USD: {result['balance_before']:.2f} "
                    f"→ {result['balance_after']:.2f}"
                )

            case "show-portfolio":
                self._show_portfolio(arguments.base)

            case "buy" | "sell":
                operation = (
                    self._portfolios.buy
                    if arguments.command == "buy"
                    else self._portfolios.sell
                )
                result = operation(
                    arguments.currency,
                    arguments.amount,
                )
                label = "Покупка" if arguments.command == "buy" else "Продажа"
                print(
                    f"{label} выполнена: "
                    f"{result['amount']:.8f} "
                    f"{result['currency_code']}"
                )
                print(f"Курс: {result['rate']:.8f} USD/{result['currency_code']}")
                print(f"Стоимость сделки: {result['total_usd']:.2f} USD")
                print(
                    f"Баланс валюты: "
                    f"{result['balance_before']:.8f} "
                    f"→ {result['balance_after']:.8f}"
                )
                print(
                    f"Баланс USD: {result['usd_before']:.2f} "
                    f"→ {result['usd_after']:.2f}"
                )

            case "get-rate":
                market = self._require_market()
                quote = market.get_rate(
                    arguments.from_code,
                    arguments.to_code,
                )
                reverse = market.get_rate(
                    arguments.to_code,
                    arguments.from_code,
                )
                print(f"Курс {quote.from_code}→{quote.to_code}: {quote.rate:.10g}")
                print(f"Обратный курс: {reverse.rate:.10g}")
                print(f"Обновлено: {quote.updated_at.isoformat()}")
                print(f"Источник: {quote.source}")

            case "update-rates":
                result = self._require_market().update_rates(arguments.source)
                print(
                    f"Обновлено курсов: {result['rates_updated']}; "
                    f"новых записей истории: "
                    f"{result['history_added']}"
                )

                for error in result["errors"]:
                    print(f"Предупреждение: {error}")

            case "show-rates":
                self._show_rates(arguments)

            case "exit":
                print("Работа завершена.")
                return False

        return True

    def _show_portfolio(self, base: str) -> None:
        """Display already calculated portfolio rows."""
        info = self._require_market().portfolio_info(
            self._portfolios,
            base,
        )
        print(f"Портфель пользователя '{info['username']}' (база: {info['base']}):")

        if not info["rows"]:
            print("Портфель пуст.")
            return

        table = PrettyTable(["Валюта", "Баланс", f"Стоимость в {info['base']}"])

        for row in info["rows"]:
            table.add_row(
                [
                    row["currency_code"],
                    f"{row['balance']:.8f}",
                    f"{row['value']:.2f}",
                ]
            )

        print(table)
        print(f"ИТОГО: {info['total']:.2f} {info['base']}")

    def _show_rates(self, arguments: Namespace) -> None:
        """Render fresh rates and any unavailable-pair warnings."""
        result = self._require_market().show_rates(
            currency=arguments.currency,
            base=arguments.base,
            top=arguments.top,
        )
        table = PrettyTable(["Пара", "Курс", "Получено", "Источник"])

        for quote in result["quotes"]:
            table.add_row(
                [
                    f"{quote.from_code}_{quote.to_code}",
                    f"{quote.rate:.10g}",
                    quote.updated_at.isoformat(),
                    quote.source,
                ]
            )

        print(table)

        for warning in result["warnings"]:
            print(f"Предупреждение: {warning}")

    def run(self) -> None:
        """Run until exit, EOF, or keyboard interruption."""
        print("ValutaTrade Hub")
        print("Только виртуальные средства. Введите help.")

        while True:
            try:
                line = input("valutatrade> ")
            except (EOFError, KeyboardInterrupt):
                print("\nРабота завершена.")
                return

            if not self.execute(line):
                return


def main() -> None:
    """Construct services and start the console application."""
    try:
        settings = SettingsLoader()
        configure_logging(settings)
        database = DatabaseManager(settings)
        auth = AuthService(database)
        rates = RateService(
            database=database,
            settings=settings,
        )
        portfolios = PortfolioService(auth, database, rates)
        market = MarketService(
            database,
            rates,
            build_updater(settings, database),
        )
        CommandInterface(auth, portfolios, market).run()

    except (ValutaTradeError, ValueError, OSError) as error:
        print(f"Не удалось запустить приложение: {error}")
