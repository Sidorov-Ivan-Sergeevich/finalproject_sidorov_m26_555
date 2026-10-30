"""Interactive command-line interface for ValutaTrade Hub."""

import shlex
from argparse import ArgumentParser, Namespace
from typing import NoReturn

from valutatrade_hub.core.exceptions import ValutaTradeError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService, PortfolioService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class CommandParser(ArgumentParser):
    """Parse commands without terminating the interactive application."""

    def error(self, message: str) -> NoReturn:
        """Convert argument errors into exceptions handled by the CLI."""
        raise ValueError(message)


def build_parser() -> CommandParser:
    """Build the available commands and their argument definitions."""
    parser = CommandParser(
        prog="valutatrade",
        description="Учебный виртуальный валютный кошелёк",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "help",
        help="Показать доступные команды",
        allow_abbrev=False,
    )

    register_parser = commands.add_parser(
        "register",
        help="Зарегистрировать пользователя",
        allow_abbrev=False,
    )
    register_parser.add_argument("--username", required=True)
    register_parser.add_argument("--password", required=True)

    login_parser = commands.add_parser(
        "login",
        help="Войти в систему",
        allow_abbrev=False,
    )
    login_parser.add_argument("--username", required=True)
    login_parser.add_argument("--password", required=True)

    commands.add_parser(
        "logout",
        help="Завершить текущую сессию",
        allow_abbrev=False,
    )

    deposit_parser = commands.add_parser(
        "deposit",
        help="Пополнить виртуальный USD-кошелёк",
        allow_abbrev=False,
    )
    deposit_parser.add_argument(
        "--amount",
        type=float,
        required=True,
    )

    commands.add_parser(
        "exit",
        help="Закрыть приложение",
        allow_abbrev=False,
    )

    return parser


class CommandInterface:
    """Read commands and delegate business operations to application services."""

    def __init__(
        self,
        auth_service: AuthService,
        portfolio_service: PortfolioService,
    ) -> None:
        """Keep shared services for the entire interactive session."""
        self._auth = auth_service
        self._portfolios = portfolio_service
        self._parser = build_parser()

    def execute(self, line: str) -> bool:
        """Execute one command and return whether the application should run."""
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
        except (ValutaTradeError, ValueError) as error:
            print(f"Ошибка: {error}")
            return True

    def _dispatch(self, arguments: Namespace) -> bool:
        """Call the appropriate service and format its result."""
        match arguments.command:
            case "help":
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

            case "exit":
                print("Работа завершена.")
                return False

        return True

    def run(self) -> None:
        """Run the interactive loop until exit or terminal interruption."""
        print("ValutaTrade Hub")
        print("Виртуальный кошелёк: операции с реальными деньгами не выполняются.")
        print("Введите help для списка команд.")

        while True:
            try:
                line = input("valutatrade> ")
            except (EOFError, KeyboardInterrupt):
                print("\nРабота завершена.")
                return

            if not self.execute(line):
                return


def main() -> None:
    """Construct application services and start the interactive interface."""
    try:
        settings = SettingsLoader()
        database = DatabaseManager(settings)
        auth_service = AuthService(database)
        rate_service = RateService(
            database=database,
            settings=settings,
        )
        portfolio_service = PortfolioService(
            auth_service,
            database,
            rate_service,
        )
    except (ValutaTradeError, ValueError) as error:
        print(f"Не удалось запустить приложение: {error}")
        return

    interface = CommandInterface(
        auth_service,
        portfolio_service,
    )
    interface.run()
