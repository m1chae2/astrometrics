"""Purpose: Turn the app's frame and stack choices into library objects.

Description: The app's RPC methods keep their old parameters: a stack or a
frame choice is named by ``camera_id`` and ``focal_length_mm``, a filter, a
file range and a time range. The library's pipelines take objects instead: a
frame list (``frames=``) and a stack record (``stack=``). This module makes
those objects through the public API:

* `matching_configurations` finds the observatory configurations whose main
  camera and telescope a camera name and a focal length describe.
* `frames_for_selection` chooses a target's frames with
  ``targets.frames.query`` and checks them as a pipeline run would, so a
  choice that matches nothing, or mixes cameras or optics, is refused before
  a job starts.
* `stack_for_equipment` finds the target's one stack of that camera and
  optic with ``processing.stacking.get_stack``.
"""

from typing import Any

from astrometricslib import FrameRecord, ObservatoryConfiguration, Target
from astrometricslib.arguments import check_frames
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.services.subject_resolver import find_camera


def _camera_matches(configuration: ObservatoryConfiguration, wanted: str, exact: Any) -> bool:
    """Say whether a configuration's main camera is the camera a name means.

    Parameters
    ----------
    configuration : `ObservatoryConfiguration`
        The configuration.
    wanted : `str`
        The camera name the app sent: a full name, an alias or part of one.
    exact : `Camera` or `None`
        The camera the name spells exactly, if any.

    Returns
    -------
    matches : `bool`
        `True` when the camera is the exact one, or when the name is part of
        the camera's name or one of its aliases, ignoring case.
    """
    camera = configuration.camera
    if exact is not None and camera.name == exact.name:
        return True
    text = wanted.strip().lower()
    return bool(text) and any(text in spelling.lower() for spelling in (camera.name, *camera.aliases))


def matching_configurations(
    astrometrics: Any, camera_id: str | None, focal_length_mm: float | None
) -> list[ObservatoryConfiguration]:
    """Find the configurations a camera name and a focal length describe.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library.
    camera_id : `str` or `None`
        The main camera: a full name, an alias or part of one. `None` means
        any camera.
    focal_length_mm : `float` or `None`
        The telescope's focal length, compared after rounding to whole
        millimeters. `None` means any telescope.

    Returns
    -------
    configurations : `list` [`ObservatoryConfiguration`]
        Every matching configuration.

    Raises
    ------
    NotFoundError
        If no configuration matches. The message names the configurations.
    """
    every = list(astrometrics.observatories.configurations.query())
    exact = find_camera(astrometrics, camera_id) if camera_id else None
    chosen = [
        configuration
        for configuration in every
        if (not camera_id or _camera_matches(configuration, camera_id, exact))
        and (
            focal_length_mm is None
            or round(float(configuration.telescope.focal_length_mm)) == round(float(focal_length_mm))
        )
    ]
    if not chosen:
        asked = f"camera {camera_id!r}" if camera_id else "any camera"
        if focal_length_mm is not None:
            asked += f" at {focal_length_mm:g} mm"
        names = ", ".join(sorted(configuration.name for configuration in every)) or "none"
        raise NotFoundError(
            f"No observatory configuration has {asked}. The configurations are: {names}.",
            details={"camera_id": camera_id, "focal_length_mm": focal_length_mm},
        )
    return chosen


def frames_for_selection(
    astrometrics: Any,
    target: Target,
    *,
    kind: str | None = None,
    filter_name: str | None = None,
    first_file: str | None = None,
    last_file: str | None = None,
    since: str | None = None,
    until: str | None = None,
    camera_id: str | None = None,
    focal_length_mm: float | None = None,
) -> list[FrameRecord]:
    """Choose a target's light frames by the app's selection, and check them.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library.
    target : `Target`
        The target.
    kind : `str`, optional
        ``"imaging"`` (default) or ``"spectral"`` frames.
    filter_name : `str`, optional
        Only frames of this filter.
    first_file, last_file : `str`, optional
        Only frames whose file name sorts in this range (``"013"`` means
        frame 013).
    since, until : `str`, optional
        Only frames taken in this time range (ISO times).
    camera_id : `str`, optional
        Only frames of this camera (a full name, an alias or part of one).
    focal_length_mm : `float`, optional
        Only frames taken at this focal length.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        The frames, from one camera and one optic, of one kind.

    Raises
    ------
    NotFoundError
        If no frame matches.

    Notes
    -----
    Frames from more than one camera or optic raise `InvalidArgumentError`
    (from ``check_frames``); the message names each group and its count.
    """
    spectral = kind == "spectral"
    query = {
        "filter_name": filter_name or ("SPEC" if spectral else None),
        "first_file": first_file,
        "last_file": last_file,
        "since": since,
        "until": until,
    }
    frames_api = astrometrics.targets.frames
    if camera_id or focal_length_mm is not None:
        chosen: dict[str, FrameRecord] = {}
        for configuration in matching_configurations(astrometrics, camera_id, focal_length_mm):
            for frame in frames_api.query(target, configuration=configuration, **query):
                chosen.setdefault(frame.path, frame)
        frames = sorted(chosen.values(), key=lambda frame: frame.timestamp or 0.0)
    else:
        frames = list(frames_api.query(target, **query))
    frames = [frame for frame in frames if frame.is_spectral == spectral]
    if not frames:
        raise NotFoundError(
            f"No {'spectral' if spectral else 'imaging'} light frames of {target.id!r} match that selection.",
            details={"target": target.id},
        )
    return check_frames(frames, target)


def stack_for_equipment(
    astrometrics: Any, target: Target, camera_id: str | None, focal_length_mm: float | None
) -> Any:
    """Find the target's one imaging stack of a camera and an optic.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library.
    target : `Target`
        The target.
    camera_id : `str` or `None`
        The camera: a full name, an alias or part of one.
    focal_length_mm : `float` or `None`
        The optic's focal length in millimeters.

    Returns
    -------
    stack : `StackConfigurationResult`
        The stack's record (``configuration_key``, ``camera``,
        ``focal_length_mm``, ``stacked_image``).

    Raises
    ------
    InvalidArgumentError
        If neither is given, or no stack or more than one matches. The
        message and ``details["configurations"]`` list the target's stacks.
    """
    keys = sorted(
        recorded.configuration_key
        for recorded in target.stacking.stacks_by_configuration.values()
        if recorded.stacked_image
    )
    details = {"target": target.id, "configurations": keys}
    if not camera_id and focal_length_mm is None:
        raise InvalidArgumentError(
            "Give a camera, a focal length or both to choose a stack.", details=details
        )
    asked = f"camera {camera_id!r}" if camera_id else "any camera"
    if focal_length_mm is not None:
        asked += f" at {focal_length_mm:g} mm"
    try:
        configurations = matching_configurations(astrometrics, camera_id, focal_length_mm)
    except NotFoundError:
        configurations = []
    stacks: dict[str, Any] = {}
    for configuration in configurations:
        stack = astrometrics.processing.stacking.get_stack(target, configuration)
        if stack is not None:
            stacks.setdefault(stack.stacked_image, stack)
    if len(stacks) == 1:
        return next(iter(stacks.values()))
    if stacks:
        raise InvalidArgumentError(
            f"Target {target.id!r} has more than one stack for {asked}: "
            f"{', '.join(sorted(stack.configuration_key for stack in stacks.values()))}. "
            "Give both the camera and the focal length.",
            details=details,
        )
    raise InvalidArgumentError(
        f"Target {target.id!r} has no stack for {asked}. Its configurations are: "
        f"{', '.join(keys) or 'none recorded (stack the target first)'}.",
        details=details,
    )
