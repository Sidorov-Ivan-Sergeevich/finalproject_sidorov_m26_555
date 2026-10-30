"""Application use cases for authentication and portfolio operations."""

from typing import Any

from valutatrade_hub.core.currencies import CryptoCurrency, get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AuthenticationError,
    CurrencyNotFoundError,
    StorageError,
    UserAlreadyExistsError,
)
from valutatrade_hub.core.models import Portfolio, User, Wallet
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.utils import validate_number
from valutatrade_hub.decorators import log_action
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service.updater import RatesUpdater


class AuthService:
    """Register users and manage an in-memory authentication session."""

    def __init__(self, database: DatabaseManager | None = None) -> None:
        """Initialize storage access and start an unauthenticated session."""
        self._database = database if database is not None else DatabaseManager()
        self._current_user: User | None = None
        self._database.initialize()

    @property
    def current_user(self) -> User | None:
        """Return the authenticated user or None."""
        return self._current_user

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Validate a username and remove surrounding whitespace."""
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Имя пользователя не может быть пустым")

        return username.strip()

    def _load_users(self) -> list[User]:
        """Restore users and reject malformed or duplicate stored records."""
        records = self._database.read("users")

        try:
            users = [User.from_dict(record) for record in records]
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise StorageError(
                "Некорректные данные пользователя в users.json"
            ) from error

        identifiers = {user.user_id for user in users}
        usernames = {user.username for user in users}

        if len(identifiers) != len(users) or len(usernames) != len(users):
            raise StorageError("В users.json обнаружены повторяющиеся ID или имена")

        return users

    def _load_portfolio_records(self) -> list[dict[str, Any]]:
        """Validate portfolio identifiers and top-level wallet containers."""
        records = self._database.read("portfolios")
        identifiers: set[int] = set()

        for record in records:
            if not isinstance(record, dict):
                raise StorageError("Некорректная запись в portfolios.json")

            user_id = record.get("user_id")

            if (
                isinstance(user_id, bool)
                or not isinstance(user_id, int)
                or user_id <= 0
                or not isinstance(record.get("wallets"), dict)
            ):
                raise StorageError("Некорректная запись в portfolios.json")

            if user_id in identifiers:
                raise StorageError("В portfolios.json обнаружены повторяющиеся ID")

            identifiers.add(user_id)

        return records

    def register(self, username: str, password: str) -> User:
        """Create a user and an empty portfolio without logging them in."""
        normalized_username = self._normalize_username(username)
        users = self._load_users()

        if any(user.username == normalized_username for user in users):
            raise UserAlreadyExistsError(normalized_username)

        portfolios = self._load_portfolio_records()
        used_ids = {user.user_id for user in users}
        used_ids.update(record["user_id"] for record in portfolios)
        next_id = max(used_ids, default=0) + 1

        user = User.create(
            user_id=next_id,
            username=normalized_username,
            password=password,
        )
        portfolio = Portfolio(user)

        updated_portfolios = [*portfolios, portfolio.to_dict()]
        updated_users = [
            *(existing_user.to_dict() for existing_user in users),
            user.to_dict(),
        ]

        self._database.write("portfolios", updated_portfolios)

        try:
            self._database.write("users", updated_users)
        except StorageError:
            try:
                self._database.write("portfolios", portfolios)
            except StorageError as rollback_error:
                raise StorageError(
                    "Регистрация не завершена, откат портфеля не удался. "
                    "Проверьте users.json и portfolios.json."
                ) from rollback_error
            raise

        return user

    def login(self, username: str, password: str) -> User:
        """Authenticate a user and replace the current session on success."""
        normalized_username = self._normalize_username(username)
        users = self._load_users()
        user = next(
            (
                candidate
                for candidate in users
                if candidate.username == normalized_username
            ),
            None,
        )

        if user is None:
            raise AuthenticationError(f"Пользователь '{normalized_username}' не найден")

        if not user.verify_password(password):
            raise AuthenticationError("Неверный пароль")

        self._current_user = user
        return user

    def logout(self) -> None:
        """Clear the current authentication session."""
        self._current_user = None

    def require_login(self) -> User:
        """Return the current user or report that authentication is required."""
        if self._current_user is None:
            raise AuthenticationError("Сначала выполните login")

        return self._current_user


class PortfolioService:
    """Manage the authenticated user's persisted portfolio."""

    def __init__(
        self,
        auth_service: AuthService,
        database: DatabaseManager,
        rate_service: RateService,
    ) -> None:
        """Use shared authentication, storage, and rate services."""
        self._auth = auth_service
        self._database = database
        self._rates = rate_service

    @staticmethod
    def _find_record(
        records: list[dict[str, Any]],
        user_id: int,
    ) -> dict[str, Any]:
        """Find one portfolio while validating record identifiers."""
        identifiers: set[int] = set()
        selected: dict[str, Any] | None = None

        for record in records:
            if not isinstance(record, dict):
                raise StorageError("Некорректная запись в portfolios.json")

            record_id = record.get("user_id")

            if (
                isinstance(record_id, bool)
                or not isinstance(record_id, int)
                or record_id <= 0
                or not isinstance(record.get("wallets"), dict)
            ):
                raise StorageError("Некорректная запись в portfolios.json")

            if record_id in identifiers:
                raise StorageError("В portfolios.json обнаружены повторяющиеся ID")

            identifiers.add(record_id)

            if record_id == user_id:
                selected = record

        if selected is None:
            raise StorageError(f"Портфель пользователя с ID {user_id} не найден")

        return selected

    def _restore_portfolio(
        self,
        user: User,
        record: dict[str, Any],
    ) -> Portfolio:
        """Restore wallet objects and reject invalid stored balances."""
        wallets: dict[str, Wallet] = {}

        try:
            for raw_code, wallet_data in record["wallets"].items():
                code = get_currency(raw_code).code

                if code in wallets:
                    raise ValueError("Повторяющийся код валюты")

                if not isinstance(wallet_data, dict):
                    raise ValueError("Некорректные данные кошелька")

                if (
                    "currency_code" in wallet_data
                    and wallet_data["currency_code"] != code
                ):
                    raise ValueError("Валюта кошелька не совпадает с ключом")

                wallets[code] = Wallet(
                    currency_code=code,
                    balance=wallet_data["balance"],
                )

        except (KeyError, TypeError, ValueError, CurrencyNotFoundError) as error:
            raise StorageError(
                f"Некорректные кошельки пользователя с ID {user.user_id}"
            ) from error

        return Portfolio(
            user=user,
            wallets=wallets,
            rate_provider=self._rates.get_rate_value,
        )

    def get_portfolio(self) -> Portfolio:
        """Load the current user's portfolio without changing stored data."""
        user = self._auth.require_login()
        records = self._database.read("portfolios")
        record = self._find_record(records, user.user_id)

        return self._restore_portfolio(user, record)

    def deposit_usd(self, amount: float) -> dict[str, Any]:
        """Add virtual USD and save the updated portfolio."""
        user = self._auth.require_login()
        validated_amount = validate_number(
            amount,
            field_name="amount",
        )
        result: dict[str, Any] = {}

        def apply_deposit(records: list[dict[str, Any]]) -> None:
            """Modify only the authenticated user's portfolio record."""
            record = self._find_record(records, user.user_id)
            portfolio = self._restore_portfolio(user, record)
            wallet = portfolio.add_currency("USD")
            balance_before = wallet.balance

            wallet.deposit(validated_amount)

            record["wallets"] = portfolio.to_dict()["wallets"]
            result.update(
                {
                    "user_id": user.user_id,
                    "username": user.username,
                    "currency_code": "USD",
                    "amount": validated_amount,
                    "balance_before": balance_before,
                    "balance_after": wallet.balance,
                }
            )

        self._database.update("portfolios", apply_deposit)
        return result

    @log_action("BUY", verbose=True)
    def buy(self, currency_code: str, amount: float) -> dict[str, Any]:
        """Buy currency units by withdrawing their current USD cost."""
        return self._trade(
            currency_code,
            amount,
            action="BUY",
        )

    @log_action("SELL", verbose=True)
    def sell(self, currency_code: str, amount: float) -> dict[str, Any]:
        """Sell currency units and deposit their current USD value."""
        return self._trade(
            currency_code,
            amount,
            action="SELL",
        )

    def _trade(
        self,
        currency_code: str,
        amount: float,
        *,
        action: str,
    ) -> dict[str, Any]:
        """Apply both wallet changes and persist one portfolio document."""
        user = self._auth.require_login()
        code = get_currency(currency_code).code
        validated_amount = validate_number(
            amount,
            field_name="amount",
        )

        if code == "USD":
            raise ValueError(
                "USD используется для расчётов. "
                "Для добавления виртуальных USD используйте пополнение."
            )

        result: dict[str, Any] = {}

        def apply_trade(records: list[dict[str, Any]]) -> None:
            """Modify temporary wallet objects before saving both balances."""
            record = self._find_record(records, user.user_id)
            portfolio = self._restore_portfolio(user, record)

            quote = self._rates.get_rate(code, "USD")
            total_usd = validate_number(
                validated_amount * quote.rate,
                field_name="Стоимость сделки",
            )

            if action == "BUY":
                currency_wallet = portfolio.add_currency(code)
                usd_wallet = portfolio.add_currency("USD")
                debit_wallet = usd_wallet
                credit_wallet = currency_wallet
                debit_amount = total_usd
                credit_amount = validated_amount
            else:
                currency_wallet = portfolio.get_wallet(code)
                usd_wallet = portfolio.add_currency("USD")
                debit_wallet = currency_wallet
                credit_wallet = usd_wallet
                debit_amount = validated_amount
                credit_amount = total_usd

            currency_before = currency_wallet.balance
            usd_before = usd_wallet.balance
            debit_before = debit_wallet.balance
            credit_before = credit_wallet.balance

            debit_wallet.withdraw(debit_amount)
            credit_wallet.deposit(credit_amount)

            if (
                debit_wallet.balance >= debit_before
                or credit_wallet.balance <= credit_before
            ):
                raise ValueError(
                    "Сумма слишком мала для изменения баланса "
                    "с текущей точностью расчётов"
                )

            record["wallets"] = portfolio.to_dict()["wallets"]
            result.update(
                {
                    "action": action,
                    "user_id": user.user_id,
                    "username": user.username,
                    "currency_code": code,
                    "amount": validated_amount,
                    "rate": quote.rate,
                    "base": "USD",
                    "total_usd": total_usd,
                    "balance_before": currency_before,
                    "balance_after": currency_wallet.balance,
                    "usd_before": usd_before,
                    "usd_after": usd_wallet.balance,
                    "updated_at": quote.updated_at.isoformat(),
                    "source": quote.source,
                }
            )

        self._database.update("portfolios", apply_trade)
        return result


class MarketService:
    """Provide rate listing, updates, and portfolio valuation for the CLI."""

    def __init__(
        self,
        database: DatabaseManager,
        rates: RateService,
        updater: RatesUpdater,
    ) -> None:
        """Use shared cache, conversion, and parser services."""
        self._database = database
        self._rates = rates
        self._updater = updater

    def get_rate(self, from_code: str, to_code: str):
        """Return a conversion quote using the core rate service."""
        return self._rates.get_rate(from_code, to_code)

    def update_rates(self, source: str | None = None) -> dict[str, Any]:
        """Request an explicit external rate update."""
        return self._updater.run_update(source)

    def show_rates(
        self,
        *,
        currency: str | None = None,
        base: str = "USD",
        top: int | None = None,
    ) -> dict[str, Any]:
        """Return available fresh quotes and report unavailable currencies."""
        base_code = get_currency(base).code

        if top is not None and top <= 0:
            raise ValueError("'top' должен быть положительным целым числом")

        document = self._database.read("rates")

        if not document["pairs"]:
            raise ApiRequestError("Локальный кеш курсов пуст. Выполните update-rates.")

        if currency is not None:
            codes = [get_currency(currency).code]
        else:
            codes = sorted(
                {
                    key.split("_")[0]
                    for key in document["pairs"]
                    if isinstance(key, str) and len(key.split("_")) == 2
                }
            )

        quotes = []
        warnings = []

        for code in codes:
            try:
                model = get_currency(code)

                if top is not None and not isinstance(
                    model,
                    CryptoCurrency,
                ):
                    continue

                quotes.append(self._rates.get_rate(code, base_code))
            except (ApiRequestError, CurrencyNotFoundError) as error:
                if currency is not None:
                    raise
                warnings.append(str(error))

        if top is not None:
            quotes.sort(key=lambda quote: quote.rate, reverse=True)
            quotes = quotes[:top]

        if not quotes:
            raise ApiRequestError(
                "Нет подходящих свежих курсов. Выполните update-rates."
            )

        return {
            "quotes": quotes,
            "warnings": warnings,
            "last_refresh": document["last_refresh"],
        }

    def portfolio_info(
        self,
        portfolios: PortfolioService,
        base: str = "USD",
    ) -> dict[str, Any]:
        """Calculate display rows and total without placing logic in the CLI."""
        portfolio = portfolios.get_portfolio()
        base_code = get_currency(base).code
        rows = []
        total = 0.0

        for code, wallet in sorted(portfolio.wallets.items()):
            if wallet.balance == 0:
                value = 0.0
            else:
                quote = self._rates.get_rate(code, base_code)
                value = validate_number(
                    wallet.balance * quote.rate,
                    field_name="Стоимость кошелька",
                    allow_zero=True,
                )

            total = validate_number(
                total + value,
                field_name="Стоимость портфеля",
                allow_zero=True,
            )
            rows.append(
                {
                    "currency_code": code,
                    "balance": wallet.balance,
                    "value": value,
                }
            )

        return {
            "username": portfolio.user.username,
            "base": base_code,
            "rows": rows,
            "total": total,
        }
