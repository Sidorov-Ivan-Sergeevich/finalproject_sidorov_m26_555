"""Shared validation helpers for domain models."""

from math import isfinite


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
