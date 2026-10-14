"""Purpose: Check and convert the arguments that the public API methods take.

Description: The public methods on `Astrometrics` and its sub-APIs accept
friendly inputs. A target may be named by its id or passed as a `Target`
object. A time may be an ISO 8601 string, a `datetime` or an astropy `Time`.
Methods that offer several variants pick one with ``kind=`` and add optional
sections with ``include=``. Every method checks these the same way, so the
checks live here, in one place:

* `resolve_target` turns a name into the `Target` it names;
* `check_choice` and `check_include` refuse values a method does not know;
* `reject_unused_arguments` refuses an argument that the chosen ``kind``
  does not use, so a caller learns about a mistake instead of having the
  argument silently ignored;
* `to_epoch_seconds` turns any accepted time into seconds since 1970.

Each check raises `InvalidArgumentError` or `NotFoundError`, never a
built-in exception.
"""

from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import Target

__all__ = [
    "TargetLookup",
    "check_choice",
    "check_include",
    "reject_unused_arguments",
    "resolve_target",
    "to_epoch_seconds",
]


class TargetLookup(Protocol):
    """Anything that can find a target by its id, such as `TargetCatalog`."""

    def get(self, target_id: str, refresh: bool = False) -> Target | None:
        """Find one target by its id.

        Parameters
        ----------
        target_id : `str`
            The target's id, matched loosely.
        refresh : `bool`, optional
            Read the stored targets first.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None` when no target has that id.
        """


def resolve_target(targets: TargetLookup | None, target: str | Target) -> Target:
    """Turn a target name into the `Target` it names.

    Parameters
    ----------
    targets : `TargetLookup` or `None`
        The catalog to look the name up in. It may be `None` when the
        caller passes `Target` objects only.
    target : `str` or `Target`
        A target id (matched loosely, as `TargetCatalog.get` does) or a
        `Target` object, which is returned as it is. A name is looked up
        after reading the stored targets again, so a target that another
        program (a frame sync, the app) added or changed is seen. That read
        takes about 0.1 s and keeps the target's unsaved edits in memory.

    Returns
    -------
    target : `Target`
        The target.

    Raises
    ------
    NotFoundError
        If the name does not match any target in the library.
    InvalidArgumentError
        If ``target`` is neither a name nor a `Target`, or a name is given
        and there is no catalog to look it up in.
    """
    if isinstance(target, Target):
        return target
    if not isinstance(target, str) or not target.strip():
        raise InvalidArgumentError("target must be a target id or a Target.")
    if targets is None:
        raise InvalidArgumentError(
            f"Pass the Target itself: there is no target catalog to look up {target!r}."
        )
    found = targets.get(target, refresh=True)
    if found is None:
        raise NotFoundError(f"No target with id {target!r} in the library.", details={"target": target})
    return found


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
