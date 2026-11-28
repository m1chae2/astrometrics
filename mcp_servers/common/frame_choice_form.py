"""Purpose: Let a client choose frames with a small filter instead of a list.

Description: A public method that works on some of a target's frames takes
them as one argument, ``frames=`` (`FrameChoice` in
`astrometricslib.arguments`): the page a frame query returns, a list of frame
records or a list of paths. A client cannot send frame records, and a long
list of paths is a poor thing to ask an AI to write. So a tool offers that
argument as a filter object:

* ``target``: whose frames (the call's own ``target`` when left out);
* ``night``: the observing night, ``YYYY-MM-DD``;
* ``since`` and ``until``: ISO 8601 times (UTC when no offset is given);
* ``filter_name``, ``first_file``, ``last_file``: the filter and a range of
  file names or frame numbers;
* ``configuration``: only frames taken with this observatory configuration,
  by its id (``observatory_configuration_query`` lists them);
* ``gain`` and ``exposure_s``: only frames of this gain and exposure length;
* ``paths``: the exact frame paths, instead of the filters above.

`build_frame_choice` makes the choice through the public library, with the
target's frame query (``targets.frames.query``), so the method gets the
frames it would get from a script.
"""

import types
import typing
from typing import Any, Union

from astrometricslib.arguments import FrameChoice
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from mcp_servers.common.resolvers import SUBJECT_KINDS, SubjectParameter, resolve_subject

FRAME_FILTER_FIELDS: dict[str, dict[str, Any]] = {
    "target": {
        "type": "string",
        "description": "The target whose frames to use. Defaults to the call's target.",
    },
    "night": {"type": "string", "description": "Only frames of this observing night, as YYYY-MM-DD."},
    "since": {
        "type": "string",
        "description": "Only frames taken at or after this ISO 8601 time (UTC if no offset).",
    },
    "until": {"type": "string", "description": "Only frames taken at or before this ISO 8601 time."},
    "filter_name": {"type": "string", "description": "Only frames of this filter, such as 'L' or 'Ha'."},
    "first_file": {"type": "string", "description": "Only frames from this file name or frame number on."},
    "last_file": {"type": "string", "description": "Only frames up to this file name or frame number."},
    "configuration": {
        "type": "string",
        "description": "Only frames taken with this observatory configuration, by its id.",
    },
    "gain": {"type": "number", "description": "Only frames with this gain (or ISO) setting."},
    "exposure_s": {"type": "number", "description": "Only frames of this exposure length, in seconds."},
    "paths": {
        "type": "array",
        "items": {"type": "string"},
        "description": "The exact frame paths. Cannot be combined with the filters.",
    },
}
"""The fields of the filter object, with their JSON schemas."""


def is_frame_choice(hint: Any) -> bool:
    """Tell whether a parameter's type hint is `FrameChoice` (or it or `None`).

    Parameters
    ----------
    hint : `Any`
        The parameter's type hint.

    Returns
    -------
    frame_choice : `bool`
        `True` when the hint allows exactly the forms of `FrameChoice`.
    """
    if typing.get_origin(hint) not in (Union, types.UnionType):
        return False
    members = {member for member in typing.get_args(hint) if member is not type(None)}
    return members == set(typing.get_args(FrameChoice))


def frame_filter_schema(description: str) -> dict[str, Any]:
    """Give the JSON schema of the filter object a client sends.

    Parameters
    ----------
    description : `str`
        The parameter's description from the docstring.

    Returns
    -------
    schema : `dict` [`str`, `Any`]
        An object schema with the fields of `FRAME_FILTER_FIELDS`.
    """
    return {
        "type": "object",
        "properties": dict(FRAME_FILTER_FIELDS),
        "additionalProperties": False,
        "description": (
            f"{description} Choose the frames with a filter (night, since/until, filter_name, "
            "first_file/last_file, configuration, gain, exposure_s) or give their paths."
        ),
    }


def build_frame_choice(api_root: Any, name: str, form: Any, call_arguments: dict[str, Any]) -> Any:
    """Turn the filter object a client sent into a frame choice.

    Parameters
    ----------
    api_root : `Astrometrics` or `Wayfinder`
        The server's API object.
    name : `str`
        The parameter's name, for the messages.
    form : `dict` or `None`
        The filter object. `None` passes through.
    call_arguments : `dict` [`str`, `Any`]
        The call's other arguments, already turned into objects. Their
        ``target`` is used when the form names none.

    Returns
    -------
    frames : `list` or `Page` or `None`
        The paths, or the frames the filter matched.

    Raises
    ------
    InvalidArgumentError
        If paths are combined with filters, or no target is named.
    NotFoundError
        If the configuration id names no configuration.

    Notes
    -----
    The target lookup raises `NotFoundError` if the target does not exist.
    """
    if form is None:
        return None
    filters = {key: value for key, value in dict(form).items() if value is not None}
    paths = filters.pop("paths", None)
    target_value = filters.pop("target", None) or call_arguments.get("target")
    if paths is not None:
        if filters:
            raise InvalidArgumentError(
                f"{name}: give paths or filters, not both.", details={"filters": sorted(filters)}
            )
        return list(paths)
    if target_value is None:
        raise InvalidArgumentError(f"{name}: say whose frames to use with 'target'.")
    target_kind = SubjectParameter(SUBJECT_KINDS["astrometricslib", "Target"])
    target = resolve_subject(api_root, target_kind, "target", target_value)
    astrometrics = api_root if hasattr(api_root, "targets") else api_root.astrometrics
    configuration_id = filters.pop("configuration", None)
    if configuration_id is not None:
        configuration = astrometrics.observatories.configurations.get(str(configuration_id))
        if configuration is None:
            raise NotFoundError(
                f"{name}: no observatory configuration with id {configuration_id!r}.",
                details={"configuration": configuration_id},
                report="error",
            )
        filters["configuration"] = configuration
    return astrometrics.targets.frames.query(target, **filters)
