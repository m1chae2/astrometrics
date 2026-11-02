"""Purpose: Small argument checks for the `wayfindinglib` public calls.

Description: Copies of the science library's checks of a choice, an
``include`` list, arguments a kind does not use, and a time, so this library
does not reach into the science library's private package. Each refuses a bad
value with `InvalidArgumentError`. Only `wayfindinglib` itself, and the tests
of either library, may import this module.
"""

from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from astrometricslib.exceptions import InvalidArgumentError

__all__ = ["check_choice", "check_include", "reject_unused_arguments", "to_epoch_seconds"]


def check_choice(name: str, value: Any, choices: Collection[str]) -> None:
    """Refuse a value that is not one of the allowed choices.

    Parameters
    ----------
    name : `str`
        The argument's name, for the message.
    value : `Any`
        The value the caller gave.
    choices : `Collection` [`str`]
        The allowed values.

    Raises
    ------
    InvalidArgumentError
        If ``value`` is not one of ``choices``.
    """
    if value not in choices:
        raise InvalidArgumentError(
            f"{name} must be one of: {', '.join(choices)}.", details={name: value, "choices": list(choices)}
        )


def check_include(include: Iterable[str] | None, allowed: Collection[str]) -> frozenset[str]:
    """Check the sections a caller asked for with ``include=``.

    Parameters
    ----------
    include : `Iterable` [`str`] or `None`
        The sections asked for. `None` means none.
    allowed : `Collection` [`str`]
        The sections the method offers.

    Returns
    -------
    sections : `frozenset` [`str`]
        The sections asked for.

    Raises
    ------
    InvalidArgumentError
        If a section is not offered, or ``include`` is a single string
        rather than a list.
    """
    if include is None:
        return frozenset()
    if isinstance(include, str):
        raise InvalidArgumentError("include must be a list of section names, not one string.")
    sections = frozenset(include)
    unknown = sorted(sections - set(allowed))
    if unknown:
        raise InvalidArgumentError(
            f"Unknown include section(s): {', '.join(unknown)}. Choose from: {', '.join(allowed)}.",
            details={"unknown": unknown, "choices": list(allowed)},
        )
    return sections


def reject_unused_arguments(
    kind: str, used_by_kind: Mapping[str, Collection[str]], given: Mapping[str, bool]
) -> None:
    """Refuse arguments that the chosen kind does not use.

    Parameters
    ----------
    kind : `str`
        The kind the caller chose.
    used_by_kind : `Mapping` [`str`, `Collection` [`str`]]
        For each kind, the names of the optional arguments it uses.
    given : `Mapping` [`str`, `bool`]
        For each optional argument, whether the caller gave it (a value
        other than its default).

    Raises
    ------
    InvalidArgumentError
        If an argument was given that ``kind`` does not use.
    """
    used = set(used_by_kind.get(kind, ()))
    unused = sorted(name for name, was_given in given.items() if was_given and name not in used)
    if unused:
        raise InvalidArgumentError(
            f"kind={kind!r} does not use: {', '.join(unused)}.", details={"kind": kind, "unused": unused}
        )


def to_epoch_seconds(value: str | datetime | Any | None, name: str = "time") -> float | None:
    """Turn a time into seconds since 1970-01-01 UTC.

    Parameters
    ----------
    value : `str`, `datetime`, `astropy.time.Time` or `None`
        An ISO 8601 string such as ``"2026-10-03T21:30:00-06:00"``, a
        `datetime`, or an astropy `Time`. A string or `datetime` with no
        time zone is taken as UTC.
    name : `str`, optional
        The argument's name, for the message.

    Returns
    -------
    seconds : `float` or `None`
        The time, or `None` if ``value`` is `None` or an empty string.

    Raises
    ------
    InvalidArgumentError
        If the value cannot be read as a time.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise InvalidArgumentError(
                f"{name} must be an ISO 8601 time, a datetime or an astropy Time: {error}",
                details={name: value},
            ) from error
    elif hasattr(value, "to_datetime") and hasattr(value, "unix"):
        return float(value.unix)
    else:
        raise InvalidArgumentError(f"{name} must be an ISO 8601 time, a datetime or an astropy Time.")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()
