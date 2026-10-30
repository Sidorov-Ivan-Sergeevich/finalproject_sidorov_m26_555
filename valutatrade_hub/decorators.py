"""Decorators for structured domain operation logging."""

import logging
from functools import wraps
from inspect import signature
from typing import Any

LOGGER = logging.getLogger("valutatrade.actions")


def log_action(action: str, *, verbose: bool = False):
    """Log successful and failed operations without swallowing exceptions."""

    def decorate(function):
        """Wrap one domain operation."""
        function_signature = signature(function)

        @wraps(function)
        def wrapper(*args, **kwargs):
            """Record only explicitly selected nonsecret operation fields."""
            bound = function_signature.bind(*args, **kwargs)
            service = bound.arguments.get("self")
            auth = getattr(service, "_auth", None)
            user = getattr(auth, "current_user", None)

            event: dict[str, Any] = {
                "action": action,
                "username": getattr(user, "username", None),
                "user_id": getattr(user, "user_id", None),
                "currency_code": bound.arguments.get("currency_code"),
                "amount": bound.arguments.get("amount"),
                "rate": None,
                "base": "USD",
            }

            try:
                result = function(*args, **kwargs)
            except Exception as error:
                event.update(
                    {
                        "result": "ERROR",
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    }
                )
                LOGGER.error(
                    "%s ERROR",
                    action,
                    extra={"event": event},
                )
                raise

            if isinstance(result, dict):
                fields = [
                    "username",
                    "user_id",
                    "currency_code",
                    "amount",
                    "rate",
                    "base",
                    "total_usd",
                ]

                if verbose:
                    fields.extend(
                        [
                            "balance_before",
                            "balance_after",
                            "usd_before",
                            "usd_after",
                        ]
                    )

                for field in fields:
                    if field in result:
                        event[field] = result[field]

            event["result"] = "OK"
            LOGGER.info(
                "%s OK",
                action,
                extra={"event": event},
            )
            return result

        return wrapper

    return decorate
