"""Shared validation helpers for domain models."""

from collections.abc import Callable, Hashable
from copy import deepcopy
from math import isfinite
from time import monotonic
from typing import Any


def validate_number(
    value: object,
    *,
    field_name: str,
    allow_zero: bool = False,
) -> float:
    """Return a finite valid number, rejecting bool and nonnumeric values."""
    requirement = "неотрицательным" if allow_zero else "положительным"
    message = f"'{field_name}' должен быть конечным {requirement} числом"

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(message)

    try:
        number = float(value)
    except OverflowError:
        raise ValueError(message) from None

    if not isfinite(number):
        raise ValueError(message)

    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError(message)

    return number


def make_ttl_cache(
    ttl_seconds: float,
) -> Callable[[Hashable, Callable[[], Any]], Any]:
    """Create a closure caching successful loader results for a limited time."""
    ttl = validate_number(
        ttl_seconds,
        field_name="TTL кэша",
    )
    cache: dict[Hashable, tuple[float, Any]] = {}

    def get_cached(
        key: Hashable,
        loader: Callable[[], Any],
    ) -> Any:
        """Return an independent cached value or load and cache a new one."""
        now = monotonic()

        expired_keys = [
            cached_key
            for cached_key, (expires_at, _) in cache.items()
            if expires_at <= now
        ]

        for expired_key in expired_keys:
            del cache[expired_key]

        if key in cache:
            return deepcopy(cache[key][1])

        value = loader()
        cache[key] = (
            monotonic() + ttl,
            deepcopy(value),
        )
        return deepcopy(value)

    return get_cached
