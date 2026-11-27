"""Purpose: Turn the id a client sends into the library object a method takes.

Description: A client sends only JSON, so it names a target, a star, an
observing session, an observation package or an observatory configuration by
its id. The library's public methods work on the objects themselves
(`Target`, `StellarObject`, `ObservationSession`, `ObservationPackage`,
`ObservatoryConfiguration`). This module is the one place that turns an id
into its object for the MCP tools. It looks the id up through the libraries'
public API (``targets.get``, ``stars.get``,
``observatories.configurations.get``, ``planning.get_plan``,
``planning.query_plans``).

`SUBJECT_KINDS` is the table: one row per object type, keyed by the type's
library and class name, so no private class is imported. A new subject type
is one more row.

`subject_kind_of` tells whether a parameter's type hint names a subject (the
object alone, or with `str` and `None` beside it, or a list of them), and
`resolve_subject` does the lookup. An id that names nothing raises
`NotFoundError` with ``report="error"``, because the client asked for it.
"""

import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Union

from astrometricslib.exceptions import NotFoundError

SUBJECT_ENTRY_METHODS = frozenset({"get", "add", "create", "resolve"})
"""Methods whose subject parameter is not looked up. ``get`` and ``resolve``
are where an id enters the library, and ``add`` and ``create`` take a new
object that is not stored yet."""


def _astrometrics(api_root: Any) -> Any:
    """Find the `Astrometrics` handle behind a server's API object.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The object a server reflects.

    Returns
    -------
    astrometrics : `Astrometrics`
        The object itself when it has ``targets``, else its
        ``astrometrics`` attribute (a `Wayfinder` holds one).
    """
    return api_root if hasattr(api_root, "targets") else api_root.astrometrics


def _find_target(api_root: Any, target_id: str) -> Any:
    """Look up a target by id (the library reads what storage changed).

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    target_id : `str`
        The id. Spaces, underscores and capital letters may differ.

    Returns
    -------
    target : `Target` or `None`
        The target, up to date with what another program wrote, or `None`.
    """
    return _astrometrics(api_root).targets.get(target_id)


def _find_configuration(api_root: Any, configuration_id: str) -> Any:
    """Look up a stored observatory configuration by id.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    configuration_id : `str`
        The configuration's id.

    Returns
    -------
    configuration : `ObservatoryConfiguration` or `None`
        The configuration, or `None`.
    """
    return _astrometrics(api_root).observatories.configurations.get(configuration_id)


def _find_star(api_root: Any, star_id: str) -> Any:
    """Look up a star by id.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    star_id : `str`
        The star's id.

    Returns
    -------
    star : `StellarObject` or `None`
        The star, or `None`.
    """
    return _astrometrics(api_root).stars.get(star_id)


def _find_session(api_root: Any, session_id: str) -> Any:
    """Look up a recorded observing session by id.

    Parameters
    ----------
    api_root : `Wayfinder`
        The server's API object.
    session_id : `str`
        The session's id.

    Returns
    -------
    session : `ObservationSession` or `None`
        The session, or `None`.
    """
    try:
        return api_root.planning.get_plan(session_id=session_id)
    except NotFoundError:
        return None


def _find_package(api_root: Any, package_id: str) -> Any:
    """Look up an observation package by id.

    Parameters
    ----------
    api_root : `Wayfinder`
        The server's API object.
    package_id : `str`
        The package's id.

    Returns
    -------
    package : `ObservationPackage` or `None`
        The package, dismissed ones included, or `None`.
    """
    packages = api_root.planning.query_plans("packages", include_dismissed=True)
    return next((package for package in packages if package.id == package_id), None)


@dataclass(frozen=True)
class SubjectKind:
    """One type of object a client names by its id.

    Attributes
    ----------
    library : `str`
        The top package the type belongs to, such as ``"astrometricslib"``.
    class_name : `str`
        The type's class name, such as ``"Target"``.
    noun : `str`
        What a person calls it, for the tool description and the messages.
    lookup : `Callable` [[`Any`, `str`], `Any`]
        Called as ``lookup(api_root, id)``; returns the object or `None`.
    """

    library: str
    class_name: str
    noun: str
    lookup: Callable[[Any, str], Any]


SUBJECT_KINDS: dict[tuple[str, str], SubjectKind] = {
    (kind.library, kind.class_name): kind
    for kind in (
        SubjectKind("astrometricslib", "Target", "target", _find_target),
        SubjectKind("astrometricslib", "StellarObject", "star", _find_star),
        SubjectKind(
            "astrometricslib", "ObservatoryConfiguration", "observatory configuration", _find_configuration
        ),
        SubjectKind("wayfindinglib", "ObservationSession", "observing session", _find_session),
        SubjectKind("wayfindinglib", "ObservationPackage", "observation package", _find_package),
    )
}
"""(library, class name) -> how to find that object from its id."""


@dataclass(frozen=True)
class SubjectParameter:
    """A parameter that takes a subject, as the reflection found it.

    Attributes
    ----------
    kind : `SubjectKind`
        The type of object.
    many : `bool`
        `True` when the parameter takes a list of them.
    """

    kind: SubjectKind
    many: bool = False


def _kind_of_type(member: Any) -> SubjectKind | None:
    """Give the subject kind of one class, if it is one.

    Parameters
    ----------
    member : `Any`
        One member of a type hint.

    Returns
    -------
    kind : `SubjectKind` or `None`
        Its row in `SUBJECT_KINDS`, or `None`.
    """
    if not isinstance(member, type):
        return None
    return SUBJECT_KINDS.get((member.__module__.split(".")[0], member.__name__))


def _union_members(hint: Any) -> list[Any]:
    """List the members of a type hint, without `None`.

    Parameters
    ----------
    hint : `Any`
        A type hint.

    Returns
    -------
    members : `list`
        The members of a union, or the hint alone.
    """
    origin = typing.get_origin(hint)
    if origin is Union or origin is types.UnionType:
        return [member for member in typing.get_args(hint) if member is not type(None)]
    return [hint]


def subject_kind_of(hint: Any) -> SubjectParameter | None:
    """Tell whether a parameter's type hint names a subject.

    A subject parameter is annotated with the object type alone or with
    `str` and `None` beside it (``str | Target | None``), or with a list of
    those (``list[Target]``). A hint that also allows something else, such
    as a sky position, is not one: the method reads that value itself.

    Parameters
    ----------
    hint : `Any`
        The parameter's type hint.

    Returns
    -------
    parameter : `SubjectParameter` or `None`
        The subject kind, or `None` when the hint is not a subject.
    """
    members = _union_members(hint)
    if len(members) == 1 and typing.get_origin(members[0]) is list:
        inner = subject_kind_of(typing.get_args(members[0])[0]) if typing.get_args(members[0]) else None
        return SubjectParameter(inner.kind, many=True) if inner is not None and not inner.many else None
    kinds = [_kind_of_type(member) for member in members if member is not str]
    if len(kinds) != 1 or kinds[0] is None:
        return None
    return SubjectParameter(kinds[0])


def resolve_subject(api_root: Any, parameter: SubjectParameter, name: str, value: Any) -> Any:
    """Turn the value a client sent for a subject parameter into the object.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    parameter : `SubjectParameter`
        What the parameter takes.
    name : `str`
        The parameter's name, for the message.
    value : `Any`
        What the client sent: an id, a list of ids, or `None`. Anything
        that is not text is passed on as it is.

    Returns
    -------
    subject : `Any`
        The object, a list of objects, or the value unchanged.

    Raises
    ------
    NotFoundError
        If an id names nothing. Its ``report`` is ``"error"``.
    """
    if parameter.many and isinstance(value, list):
        return [resolve_subject(api_root, SubjectParameter(parameter.kind), name, item) for item in value]
    if not isinstance(value, str):
        return value
    found = parameter.kind.lookup(api_root, value)
    if found is None:
        raise NotFoundError(
            f"No {parameter.kind.noun} with id {value!r} (argument {name!r}).",
            details={name: value},
            report="error",
        )
    return found


def subject_schema(parameter: SubjectParameter, description: str) -> dict[str, Any]:
    """Give the JSON schema a client sees for a subject parameter.

    Parameters
    ----------
    parameter : `SubjectParameter`
        What the parameter takes.
    description : `str`
        The parameter's description from the docstring.

    Returns
    -------
    schema : `dict` [`str`, `Any`]
        A string id, or an array of them.
    """
    text = f"{description} Give the {parameter.kind.noun}'s id."
    if parameter.many:
        return {"type": "array", "items": {"type": "string"}, "description": text}
    return {"type": "string", "description": text}
