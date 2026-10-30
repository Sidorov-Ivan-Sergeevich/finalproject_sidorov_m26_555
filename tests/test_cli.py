"""Tests for interactive command parsing and service integration."""

from pathlib import Path

import pytest

from valutatrade_hub.cli.interface import CommandInterface
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService, PortfolioService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture
def cli_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[CommandInterface, AuthService, DatabaseManager]:
    """Create the CLI with real services and temporary JSON storage."""
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.chdir(tmp_path)

    settings = SettingsLoader()
    database = DatabaseManager(settings)
    auth = AuthService(database)
    rates = RateService(database=database, settings=settings)
    portfolios = PortfolioService(auth, database, rates)
    interface = CommandInterface(auth, portfolios)

    return interface, auth, database


@pytest.mark.parametrize("line", ["", "   "])
def test_empty_input_keeps_application_running(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    line: str,
) -> None:
    """Blank input performs no operation."""
    interface, _, database = cli_services

    assert interface.execute(line) is True
    assert database.read("users") == []


@pytest.mark.parametrize("line", ["help", "--help", "register --help"])
def test_help_keeps_application_running(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
    line: str,
) -> None:
    """Help is printed without terminating the interactive session."""
    interface, _, _ = cli_services

    assert interface.execute(line) is True
    assert "usage:" in capsys.readouterr().out


def test_register_command(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Registration delegates to the service and prints public information."""
    interface, auth, database = cli_services

    interface.execute("register --username alice --password 1234")

    output = capsys.readouterr().out

    assert "зарегистрирован" in output
    assert "alice" in output
    assert "1234" not in output
    assert len(database.read("users")) == 1
    assert auth.current_user is None


def test_quoted_values_are_preserved(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
) -> None:
    """Quoted spaces and the word where remain part of argument values."""
    interface, auth, _ = cli_services

    interface.execute('register --username "alice where test" --password "ab cd"')
    interface.execute('login --username "alice where test" --password "ab cd"')

    assert auth.current_user is not None
    assert auth.current_user.username == "alice where test"
    assert auth.current_user.verify_password("ab cd")


def test_login_and_deposit_share_session(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A successful login remains active for the following command."""
    interface, _, database = cli_services

    interface.execute("register --username alice --password 1234")
    interface.execute("login --username alice --password 1234")
    interface.execute("deposit --amount 100")

    output = capsys.readouterr().out
    wallets = database.read("portfolios")[0]["wallets"]

    assert "Вы вошли как 'alice'" in output
    assert "Пополнение выполнено: 100.00 USD" in output
    assert wallets["USD"]["balance"] == 100.0


def test_deposit_requires_login(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI displays the service authentication error."""
    interface, _, database = cli_services

    assert interface.execute("deposit --amount 100") is True

    assert "Сначала выполните login" in capsys.readouterr().out
    assert database.read("portfolios") == []


def test_wrong_password_is_reported(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An incorrect password does not terminate the application."""
    interface, auth, _ = cli_services
    interface.execute("register --username alice --password 1234")

    assert interface.execute("login --username alice --password wrong") is True

    assert "Неверный пароль" in capsys.readouterr().out
    assert auth.current_user is None


def test_duplicate_registration_is_reported(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A duplicate username produces a friendly error."""
    interface, _, database = cli_services
    interface.execute("register --username alice --password 1234")

    interface.execute("register --username alice --password 5678")

    assert "уже занято" in capsys.readouterr().out
    assert len(database.read("users")) == 1


def test_logout_clears_session(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
) -> None:
    """Logout delegates to the shared authentication service."""
    interface, auth, _ = cli_services
    interface.execute("register --username alice --password 1234")
    interface.execute("login --username alice --password 1234")

    assert interface.execute("logout") is True
    assert auth.current_user is None


@pytest.mark.parametrize(
    "line",
    [
        "unknown-command",
        "register --username alice",
        "login",
        "deposit --amount not-a-number",
        "deposit --amount -1",
        "deposit --amount nan",
        'register --username "alice',
        "exit unexpected-argument",
    ],
)
def test_bad_command_does_not_close_application(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    capsys: pytest.CaptureFixture[str],
    line: str,
) -> None:
    """Parsing and validation errors leave storage and the loop intact."""
    interface, _, database = cli_services
    interface.execute("register --username alice --password 1234")
    interface.execute("login --username alice --password 1234")
    original = database.read("portfolios")
    capsys.readouterr()

    assert interface.execute(line) is True

    assert "Ошибка:" in capsys.readouterr().out
    assert database.read("portfolios") == original


def test_exit_returns_stop_signal(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
) -> None:
    """The exit command tells the loop to stop."""
    interface, _, _ = cli_services

    assert interface.execute("exit") is False


def test_interactive_loop_runs_until_exit(
    cli_services: tuple[CommandInterface, AuthService, DatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The loop can process several commands within the same session."""
    interface, _, _ = cli_services
    commands = iter(["help", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    interface.run()

    output = capsys.readouterr().out

    assert "ValutaTrade Hub" in output
    assert "Работа завершена." in output
