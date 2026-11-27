"""Purpose: Turn the id a client sends into the library object a method takes.

Description: A client sends only JSON, so it names a target, a star, an
observing session, an observation package, an observatory configuration or a
job by its id. The library's public methods work on the objects themselves
(`Target`, `StellarObject`, `ObservationSession`, `ObservationPackage`,
`ObservatoryConfiguration`, `JobSummary`). This module is the one place that
turns an id into its object for the MCP tools. It looks the id up through the
libraries' public API (``targets.get``, ``stars.get``,
``observatories.configurations.get``, ``jobs.get``,
``planning.sessions.query``, ``planning.packages.get``).

`SUBJECT_KINDS` is the table: one row per object type, keyed by the type's
library and class name, so no private class is imported. A new subject type
is one more row. A camera is named by any spelling of its name: the cameras
in the settings and in the stored observatory configurations are searched by
name, by alias and by the names their noise profiles list.

`subject_kind_of` tells whether a parameter's type hint names a subject (the
object alone, or with `str` and `None` beside it, or a list of them), and
`resolve_subject` does the lookup. A *sky object* (a parameter typed
``Target | StellarObject``, such as the objects of
``planning.sky.get_visibility``) is named by any name the library or SIMBAD
knows (``targets.resolve``), or sent as ``{"id", "ra_deg", "dec_deg"}`` for a
point with a known position. An id that names nothing raises
`NotFoundError` with ``report="error"``, because the client asked for it.
"""

import re
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


def _find_job(api_root: Any, job_id: str) -> Any:
    """Look up a recorded job by id.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    job_id : `str`
        The job's id.

    Returns
    -------
    job : `JobSummary` or `None`
        The job, or `None`.
    """
    return _astrometrics(api_root).jobs.get(job_id)


def _find_session(api_root: Any, session_id: str) -> Any:
    """Look up a recorded observing session by id or by its night.

    Parameters
    ----------
    api_root : `Wayfinder`
        The server's API object.
    session_id : `str`
        The session's id, or its observing night such as ``"2026-09-24"``.

    Returns
    -------
    session : `ObservationSession` or `None`
        The session, or `None`.
    """
    return next(
        (
            session
            for session in api_root.planning.sessions.query()
            if session_id in (session.id, session.night_date.isoformat())
        ),
        None,
    )


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
    return api_root.planning.packages.get(package_id)


def _find_sky_object(api_root: Any, name: str) -> Any:
    """Look up a named object: a library target, a library star or SIMBAD's.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    name : `str`
        A target id or common name, a star name, or a catalog name.

    Returns
    -------
    sky_object : `Target`, `StellarObject` or `None`
        The library target or star; for a name only SIMBAD knows, a point
        with that name at SIMBAD's position; `None` when nobody knows it.
    """
    from astrometricslib import StellarObject

    resolution = _astrometrics(api_root).targets.resolve(name)
    if resolution is None:
        return None
    if resolution.target is not None:
        return resolution.target
    if resolution.star is not None:
        return resolution.star
    return StellarObject(id=name, name=resolution.name, ra=resolution.ra_deg, dec=resolution.dec_deg)


def _sky_point(value: dict[str, Any]) -> Any:
    """Build a point on the sky from ``{"id", "ra_deg", "dec_deg"}``.

    Parameters
    ----------
    value : `dict` [`str`, `Any`]
        The point the client sent; ``name`` is optional.

    Returns
    -------
    point : `StellarObject`
        A star-shaped point at that position.

    Raises
    ------
    InvalidArgumentError
        If a key is missing or a position is not a number.
    """
    from astrometricslib import StellarObject
    from astrometricslib.exceptions import InvalidArgumentError

    try:
        return StellarObject(
            id=str(value["id"]),
            name=str(value.get("name", value["id"])),
            ra=float(value["ra_deg"]),
            dec=float(value["dec_deg"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidArgumentError(
            'A point on the sky needs {"id": ..., "ra_deg": ..., "dec_deg": ...}.', details={"point": value}
        ) from error


def _spelling(name: str | None) -> str:
    """Reduce a camera name to lowercase letters and digits.

    Parameters
    ----------
    name : `str` or `None`
        A camera name.

    Returns
    -------
    spelling : `str`
        The name without case, spaces or punctuation.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").casefold())


def _find_camera(api_root: Any, name: str) -> Any:
    """Look up a camera by any spelling of its name.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    name : `str`
        The camera's name, an alias, or a name its noise profile lists.

    Returns
    -------
    camera : `Camera` or `None`
        The camera from the settings, else from a stored observatory
        configuration, or `None`.
    """
    astrometrics = _astrometrics(api_root)
    wanted = _spelling(name)
    cameras = list(astrometrics.config.get_cameras())
    for configuration in astrometrics.observatories.configurations.query():
        cameras.extend(camera for camera in (configuration.camera, configuration.guide_camera) if camera)
    for camera in cameras:
        if wanted in {_spelling(spelling) for spelling in (camera.name, *camera.aliases)}:
            return camera
    for camera in cameras:
        profile = astrometrics.calibration.get_profile(camera)
        if not profile.is_generic_fallback and wanted in {
            _spelling(spelling)
            for spelling in (profile.camera_name, profile.record_name, *profile.name_aliases)
        }:
            return camera
    return None


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
    from_mapping : `Callable` [[`dict`], `Any`] or `None`
        For a kind a client may also send as a JSON object, builds the
        object from it.
    """

    library: str
    class_name: str
    noun: str
    lookup: Callable[[Any, str], Any]
    from_mapping: Callable[[dict[str, Any]], Any] | None = None


SUBJECT_KINDS: dict[tuple[str, str], SubjectKind] = {
    (kind.library, kind.class_name): kind
    for kind in (
        SubjectKind("astrometricslib", "Target", "target", _find_target),
        SubjectKind("astrometricslib", "StellarObject", "star", _find_star),
        SubjectKind(
            "astrometricslib", "ObservatoryConfiguration", "observatory configuration", _find_configuration
        ),
        SubjectKind("astrometricslib", "Camera", "camera", _find_camera),
        SubjectKind("astrometricslib", "JobSummary", "job", _find_job),
        SubjectKind("wayfindinglib", "ObservationSession", "observing session", _find_session),
        SubjectKind("wayfindinglib", "ObservationPackage", "observation package", _find_package),
    )
}
"""(library, class name) -> how to find that object from its id."""

SKY_OBJECT = SubjectKind(
    "astrometricslib", "Target | StellarObject", "sky object", _find_sky_object, _sky_point
)
"""A target or a star, named by any name, or a point sent with its position."""


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
    # A `SkyPosition` beside a target and a star is what a sent point becomes.
    kinds = [
        _kind_of_type(member)
        for member in members
        if member is not str and getattr(member, "__name__", "") != "SkyPosition"
    ]
    if {kind.class_name for kind in kinds if kind is not None} == {"Target", "StellarObject"} and len(
        kinds
    ) == 2:
        return SubjectParameter(SKY_OBJECT)
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
    if isinstance(value, dict) and parameter.kind.from_mapping is not None:
        return parameter.kind.from_mapping(value)
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
    if parameter.kind is SKY_OBJECT:
        text = f'{description} Give a name, or {{"id", "ra_deg", "dec_deg"}} for a point (ICRS degrees).'
        item: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
        if parameter.many:
            return {"type": "array", "items": item, "description": text}
        return {**item, "description": text}
    text = f"{description} Give the {parameter.kind.noun}'s id."
    if parameter.many:
        return {"type": "array", "items": {"type": "string"}, "description": text}
    return {"type": "string", "description": text}
