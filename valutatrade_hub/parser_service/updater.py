"""Coordinate API clients and persist successful rate updates."""

import logging
from typing import Any

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage

LOGGER = logging.getLogger("valutatrade.parser")


class RatesUpdater:
    """Fetch selected providers without discarding other successful results."""

    def __init__(
        self,
        clients: list[BaseApiClient],
        storage: RatesStorage,
    ) -> None:
        """Receive provider clients and storage through dependency injection."""
        self._clients = clients
        self._storage = storage

    def run_update(self, source: str | None = None) -> dict[str, Any]:
        """Update available rates and report partial provider failures."""
        selected = [
            client
            for client in self._clients
            if source is None or client.name == source
        ]

        if not selected:
            raise ValueError("Неизвестный источник курсов")

        LOGGER.info(
            "Starting rates update",
            extra={"event": {"source": source or "all"}},
        )

        records = []
        errors = []

        for client in selected:
            try:
                rates = client.fetch_rates()

                if not rates:
                    raise ApiRequestError(
                        f"{client.source}: получен пустой набор курсов"
                    )

                for pair, rate in rates.items():
                    from_code, to_code = pair.split("_")
                    metadata = client.metadata[pair]
                    timestamp = metadata["received_at"]

                    records.append(
                        {
                            "id": f"{pair}_{timestamp}",
                            "from_currency": from_code,
                            "to_currency": to_code,
                            "rate": rate,
                            "timestamp": timestamp,
                            "source": client.source,
                            "meta": metadata,
                        }
                    )

                LOGGER.info(
                    "Provider update successful",
                    extra={
                        "event": {
                            "source": client.source,
                            "count": len(rates),
                            "result": "OK",
                        }
                    },
                )

            except ApiRequestError as error:
                errors.append(str(error))
                LOGGER.error(
                    "Provider update failed",
                    extra={
                        "event": {
                            "source": client.source,
                            "result": "ERROR",
                            "error_message": str(error),
                        }
                    },
                )

        if not records:
            raise ApiRequestError(
                "Не удалось обновить ни одного курса. " + "; ".join(errors)
            )

        counts = self._storage.save(records)

        LOGGER.info(
            "Rates update completed",
            extra={
                "event": {
                    **counts,
                    "errors": errors,
                    "result": "PARTIAL" if errors else "OK",
                }
            },
        )

        return {
            **counts,
            "errors": errors,
        }


def build_updater(
    settings: SettingsLoader,
    database: DatabaseManager,
) -> RatesUpdater:
    """Construct the standard updater using both external providers."""
    config = ParserConfig.from_settings(settings)

    return RatesUpdater(
        clients=[
            CoinGeckoClient(config),
            ExchangeRateApiClient(config),
        ],
        storage=RatesStorage(database),
    )
