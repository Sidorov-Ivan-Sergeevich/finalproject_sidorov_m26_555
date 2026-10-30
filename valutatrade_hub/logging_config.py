"""Configure JSON application logs with file rotation."""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from math import isfinite
from typing import Any

from valutatrade_hub.infra.settings import SettingsLoader


def _safe_json(value: Any) -> Any:
    """Convert nonfinite numbers and nested values into valid JSON data."""
    if isinstance(value, float) and not isfinite(value):
        return str(value)

    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]

    return value


class JsonFormatter(logging.Formatter):
    """Format a log record and optional structured event as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize timestamp, level, message, and structured fields."""
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                UTC,
            ).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "event", {}))

        return json.dumps(
            _safe_json(payload),
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        )


def configure_logging(settings: SettingsLoader | None = None) -> None:
    """Configure separate rotating logs for actions and parser operations."""
    configuration = settings if settings is not None else SettingsLoader()

    logs = {
        "valutatrade.actions": configuration.get("actions_log_path"),
        "valutatrade.parser": configuration.get("parser_log_path"),
    }

    for logger_name, path in logs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(logger_name)
        logger.setLevel(configuration.get("log_level"))
        logger.propagate = False

        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        handler = RotatingFileHandler(
            filename=path,
            maxBytes=configuration.get("log_max_bytes"),
            backupCount=configuration.get("log_backup_count"),
            encoding="utf-8",
        )

        if configuration.get("log_format") == "json":
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(levelname)s %(asctime)s %(message)s %(event)s"
            )

        handler.setFormatter(formatter)
        logger.addHandler(handler)
