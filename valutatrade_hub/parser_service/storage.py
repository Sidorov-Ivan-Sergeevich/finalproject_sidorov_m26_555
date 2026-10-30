"""Persist rate history and the latest validated cache snapshot."""

from datetime import UTC, datetime
from typing import Any

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.utils import validate_number
from valutatrade_hub.infra.database import DatabaseManager


class RatesStorage:
    """Save history and latest quotes using the existing JSON storage."""

    def __init__(self, database: DatabaseManager) -> None:
        """Use the application's configured document paths."""
        self._database = database

    def save(self, records: list[dict[str, Any]]) -> dict[str, int]:
        """Deduplicate history and replace only older cached pair values."""
        for record in records:
            try:
                validate_number(record["rate"], field_name="Курс")
                timestamp = datetime.fromisoformat(record["timestamp"])

                if timestamp.tzinfo is None:
                    raise ValueError("Часовой пояс отсутствует")

                if not record["source"]:
                    raise ValueError("Источник отсутствует")
            except (KeyError, TypeError, ValueError) as error:
                raise StorageError("Нельзя сохранить некорректную котировку") from error

        counts = {"history_added": 0, "rates_updated": 0}

        def merge_history(history: list[dict[str, Any]]) -> None:
            seen = set()

            for item in history:
                if not isinstance(item, dict) or "id" not in item:
                    raise StorageError("Некорректная история курсов")
                seen.add(item["id"])

            for record in records:
                if record["id"] not in seen:
                    history.append(record)
                    seen.add(record["id"])
                    counts["history_added"] += 1

        def merge_snapshot(document: dict[str, Any]) -> None:
            pairs = document["pairs"]

            for record in records:
                key = f"{record['from_currency']}_{record['to_currency']}"
                incoming_time = datetime.fromisoformat(record["timestamp"])
                previous = pairs.get(key)

                if previous is not None:
                    try:
                        previous_time = datetime.fromisoformat(previous["updated_at"])
                        if previous_time.tzinfo is None:
                            raise ValueError("Часовой пояс отсутствует")
                    except (KeyError, TypeError, ValueError) as error:
                        raise StorageError(
                            f"Некорректное время в кэше пары {key}"
                        ) from error

                    if incoming_time <= previous_time:
                        continue

                pairs[key] = {
                    "rate": record["rate"],
                    "updated_at": record["timestamp"],
                    "source": record["source"],
                    "source_timestamp": record["meta"].get("source_timestamp"),
                }
                counts["rates_updated"] += 1

            if counts["rates_updated"]:
                document["last_refresh"] = datetime.now(UTC).isoformat()

        self._database.update("history", merge_history)
        self._database.update("rates", merge_snapshot)

        return counts
