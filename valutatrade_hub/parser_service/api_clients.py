"""HTTP clients normalizing external rates into currency-to-USD pairs."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import make_ttl_cache, validate_number
from valutatrade_hub.parser_service.config import (
    CRYPTO_ID_MAP,
    ParserConfig,
)


class BaseApiClient(ABC):
    """Provide a shared rate fetching interface and safe HTTP handling."""

    source: str
    name: str

    def __init__(self, config: ParserConfig) -> None:
        """Keep configuration, response metadata, and a closure-based cache."""
        self.config = config
        self.metadata: dict[str, dict[str, Any]] = {}
        self._cached = make_ttl_cache(config.response_cache_seconds)

    def _request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch and briefly cache a successful response without logging URLs."""
        cache_key = (
            url,
            tuple(sorted((params or {}).items())),
            tuple(sorted((headers or {}).items())),
        )

        def download() -> tuple[dict[str, Any], dict[str, Any]]:
            response = None
            started = perf_counter()

            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.config.request_timeout,
                )

                status = response.status_code

                if status in (401, 403):
                    raise ApiRequestError(
                        f"{self.source}: доступ запрещён, проверьте API-ключ"
                    )

                if status == 429:
                    raise ApiRequestError(f"{self.source}: превышен лимит запросов")

                response.raise_for_status()
                payload = response.json()

                if not isinstance(payload, dict):
                    raise ApiRequestError(f"{self.source}: ожидался JSON-объект")

                metadata = {
                    "received_at": datetime.now(UTC).isoformat(),
                    "request_ms": round(
                        (perf_counter() - started) * 1000,
                        2,
                    ),
                    "status_code": status,
                    "etag": response.headers.get("ETag"),
                }

                return payload, metadata

            except requests.Timeout as error:
                raise ApiRequestError(
                    f"{self.source}: превышено время ожидания"
                ) from error
            except requests.RequestException as error:
                raise ApiRequestError(
                    f"{self.source}: сетевая ошибка или HTTP-сбой"
                ) from error
            except ValueError as error:
                raise ApiRequestError(f"{self.source}: некорректный JSON") from error
            finally:
                if response is not None:
                    response.close()

        return self._cached(cache_key, download)

    @abstractmethod
    def fetch_rates(self) -> dict[str, float]:
        """Return normalized pairs and fill metadata for each pair."""
        ...


class CoinGeckoClient(BaseApiClient):
    """Fetch cryptocurrency prices using CoinGecko coin identifiers."""

    source = "CoinGecko"
    name = "coingecko"

    def fetch_rates(self) -> dict[str, float]:
        """Return supported cryptocurrency prices in USD."""
        headers = {}

        if self.config.coingecko_api_key:
            headers["x-cg-demo-api-key"] = self.config.coingecko_api_key

        payload, metadata = self._request(
            self.config.coingecko_url,
            params={
                "ids": ",".join(CRYPTO_ID_MAP.values()),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
            headers=headers,
        )

        rates = {}
        self.metadata = {}

        try:
            for code, raw_id in CRYPTO_ID_MAP.items():
                item = payload[raw_id]
                pair = f"{code}_USD"
                rates[pair] = validate_number(
                    item["usd"],
                    field_name="Курс",
                )
                self.metadata[pair] = {
                    **metadata,
                    "raw_id": raw_id,
                    "source_timestamp": item.get("last_updated_at"),
                }
        except (KeyError, TypeError, ValueError) as error:
            raise ApiRequestError(
                "CoinGecko: ответ содержит неполные или некорректные курсы"
            ) from error

        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Fetch fiat exchange rates and normalize their direction."""

    source = "ExchangeRate-API"
    name = "exchangerate"

    def fetch_rates(self) -> dict[str, float]:
        """Invert USD-to-fiat quotes into fiat-to-USD prices."""
        key = self.config.exchangerate_api_key

        if not key:
            raise ApiRequestError("ExchangeRate-API: задайте EXCHANGERATE_API_KEY")

        url = f"{self.config.exchangerate_url}/{key}/latest/{self.config.base_currency}"
        payload, metadata = self._request(url)

        if payload.get("result") != "success":
            raise ApiRequestError("ExchangeRate-API: запрос отклонён провайдером")

        if payload.get("base_code") != "USD":
            raise ApiRequestError("ExchangeRate-API: ожидалась базовая валюта USD")

        rates = {}
        self.metadata = {}

        try:
            conversions = payload["conversion_rates"]

            for code in self.config.fiat_currencies:
                usd_to_currency = validate_number(
                    conversions[code],
                    field_name="Курс",
                )
                pair = f"{code}_USD"
                rates[pair] = validate_number(
                    1.0 / usd_to_currency,
                    field_name="Курс",
                )
                self.metadata[pair] = {
                    **metadata,
                    "source_timestamp": payload.get("time_last_update_unix"),
                }
        except (KeyError, TypeError, ValueError) as error:
            raise ApiRequestError(
                "ExchangeRate-API: некорректные курсы в ответе"
            ) from error

        return rates
