"""Read and convert exchange rates from the local JSON cache."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import validate_number
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@dataclass(frozen=True)
class RateQuote:
    """Exchange rate and metadata for one conversion direction."""

    from_code: str
    to_code: str
    rate: float
    updated_at: datetime
    source: str


class RateService:
    """Resolve fresh direct, inverse, and USD cross-rates."""

    def __init__(
        self,
        database: DatabaseManager | None = None,
        settings: SettingsLoader | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Configure storage, freshness settings, and an optional test clock."""
        self._settings = settings if settings is not None else SettingsLoader()
        self._database = (
            database if database is not None else DatabaseManager(self._settings)
        )
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._database.initialize()

    def get_rate(self, from_code: str, to_code: str) -> RateQuote:
        """Return a fresh conversion quote or report unavailable data."""
        source_code = get_currency(from_code).code
        target_code = get_currency(to_code).code
        now = self._clock().astimezone(UTC)

        if source_code == target_code:
            return RateQuote(
                from_code=source_code,
                to_code=target_code,
                rate=1.0,
                updated_at=now,
                source="identity",
            )

        pairs = self._database.read("rates")["pairs"]

        quote = self._find_quote(
            pairs,
            source_code,
            target_code,
            now,
        )

        if quote is not None:
            return quote

        if source_code != "USD" and target_code != "USD":
            source_to_usd = self._find_quote(
                pairs,
                source_code,
                "USD",
                now,
            )
            target_to_usd = self._find_quote(
                pairs,
                target_code,
                "USD",
                now,
            )

            if source_to_usd is not None and target_to_usd is not None:
                try:
                    cross_rate = validate_number(
                        source_to_usd.rate / target_to_usd.rate,
                        field_name="Курс",
                    )
                except ValueError as error:
                    raise ApiRequestError(
                        f"Невозможно вычислить курс {source_code}→{target_code}"
                    ) from error

                sources = sorted({source_to_usd.source, target_to_usd.source})

                return RateQuote(
                    from_code=source_code,
                    to_code=target_code,
                    rate=cross_rate,
                    updated_at=min(
                        source_to_usd.updated_at,
                        target_to_usd.updated_at,
                    ),
                    source=" + ".join(sources),
                )

        raise ApiRequestError(
            f"Нет свежего курса {source_code}→{target_code}: "
            "данные отсутствуют, устарели или некорректны. "
            "Выполните update-rates."
        )

    def get_rate_value(self, from_code: str, to_code: str) -> float:
        """Return only the numeric rate for portfolio valuation."""
        return self.get_rate(from_code, to_code).rate

    def _find_quote(
        self,
        pairs: dict[str, Any],
        from_code: str,
        to_code: str,
        now: datetime,
    ) -> RateQuote | None:
        """Try the direct pair first, then the inverse pair."""
        candidates = (
            (f"{from_code}_{to_code}", False),
            (f"{to_code}_{from_code}", True),
        )
        ttl = self._settings.get("rates_ttl_seconds")

        for pair_key, inverse in candidates:
            record = pairs.get(pair_key)

            if not isinstance(record, dict):
                continue

            try:
                rate = validate_number(
                    record.get("rate"),
                    field_name="Курс",
                )
                timestamp = record.get("updated_at")

                if not isinstance(timestamp, str):
                    continue

                updated_at = datetime.fromisoformat(timestamp)

                if updated_at.tzinfo is None:
                    continue

                updated_at = updated_at.astimezone(UTC)
                age = (now - updated_at).total_seconds()

                if age < 0 or age >= ttl:
                    continue

                source = record.get("source")

                if not isinstance(source, str) or not source.strip():
                    continue

                if inverse:
                    rate = validate_number(
                        1.0 / rate,
                        field_name="Курс",
                    )

            except (ValueError, OverflowError):
                continue

            return RateQuote(
                from_code=from_code,
                to_code=to_code,
                rate=rate,
                updated_at=updated_at,
                source=source.strip(),
            )

        return None
