"""Purpose: The argument rules every public method of both libraries shares.

Description: A public method checks its arguments the same way in
astrometricslib and in wayfindinglib, so the rules live here, once. Scripts
and wayfindinglib import them from the public module
`astrometricslib.arguments`.

* `TimeInput` names the kinds of time a method takes, and `to_epoch_seconds`
  turns any of them into seconds since 1970. A time with no time zone is UTC.
* `check_choice` and `check_include` refuse a value a method does not know.
* `check_limit` holds the one ``limit`` rule: a count outside the documented
  range is refused, never quietly moved into it.
* `reject_unused_arguments` refuses an argument that the chosen ``kind`` does
  not use.

Each check raises `InvalidArgumentError`, never a built-in exception.
"""

from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from astropy.time import Time

from astrometricslib.foundation.errors import InvalidArgumentError

__all__ = [
    "TimeInput",
    "check_choice",
    "check_include",
    "check_limit",
    "reject_unused_arguments",
    "to_epoch_seconds",
]


TimeInput = str | datetime | Time
"""A time as a public method takes it: ISO 8601 text, a `datetime` or an
astropy `Time`. A time with no time zone is UTC. A method whose time is
optional says ``TimeInput | None``."""


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
    """Check the parts a caller asked for with ``include=``.

    Parameters
    ----------
    include : `Iterable` [`str`] or `None`
        The parts asked for. `None` means none.
    allowed : `Collection` [`str`]
        The parts the method offers.

    Returns
    -------
    sections : `frozenset` [`str`]
        The parts asked for.

    Raises
    ------
    InvalidArgumentError
        If a part is not offered, or ``include`` is a single string rather
        than a list.
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


def check_limit(
    value: int | None,
    minimum: int,
    maximum: int | None,
    *,
    name: str = "limit",
    allow_none: bool = True,
) -> None:
    """Refuse a count (``limit``, ``offset``, ``lines``) outside its range.

    This is the one ``limit`` rule of the public methods: a value outside
    the documented range raises, it is never clamped. `None` is accepted
    when ``allow_none`` is set; each method says what `None` means.

    Parameters
    ----------
    value : `int` or `None`
        The count the caller gave.
    minimum : `int`
        The smallest allowed value.
    maximum : `int` or `None`
        The largest allowed value, or `None` for no upper bound.
    name : `str`, optional
        The argument's name, for the message. Defaults to ``"limit"``.
    allow_none : `bool`, optional
        Accept `None`. Defaults to `True`.

    Raises
    ------
    InvalidArgumentError
        If ``value`` is not a whole number (or `None` when allowed), or is
        outside ``minimum`` to ``maximum``.
    """
    if value is None and allow_none:
        return
    upper = "" if maximum is None else f" and at most {maximum}"
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidArgumentError(
            f"{name} must be a whole number of at least {minimum}{upper}.", details={name: value}
        )
    if value < minimum or (maximum is not None and value > maximum):
        raise InvalidArgumentError(
            f"{name} must be at least {minimum}{upper}; got {value}.",
            details={name: value, "minimum": minimum, "maximum": maximum},
        )


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


def to_epoch_seconds(value: TimeInput | None, name: str = "time") -> float | None:
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
