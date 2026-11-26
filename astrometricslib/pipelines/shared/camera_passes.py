"""Plan which cameras a batch run processes, and which targets each one gets.

A target record holds one stack and one set of quality summaries, not one per
camera. If two cameras were both processed for the same target, the second
would overwrite the first. So each target is handed to a single camera: the
first one, in priority order, that has frames of it.

The cameras and their order come from the setups in the config file (see
`astrometricslib.foundation.observatory_setups`), not from names written into
a script.
"""

import logging
from dataclasses import dataclass
from typing import Any

from astrometricslib._internal.drivers.camera_profile_store import camera_identity, record_name_for_camera
from astrometricslib.foundation.observatory_setups import ObservatorySetups
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.analysis_configuration import AnalysisConfiguration, stack_configuration
from astrometricslib.pipelines.shared.frame_grouping import (
    select_frames_for_camera,
    select_frames_for_processing,
    split_standard_and_spectral_frames,
)

logger = logging.getLogger(__name__)


def camera_pass_order(
    observatory_setups: ObservatorySetups, primary_camera_name: str | None, config: Any = None
) -> list[str]:
    """List the cameras to process, best first.

    The primary camera comes first when it is used in a setup. The others
    follow in the order their first setup appears in the config. A camera that
    appears in several setups is listed once.

    Parameters
    ----------
    observatory_setups : `ObservatorySetups`
        The optics and setups from the config.
    primary_camera_name : `str` or `None`
        The observer's primary camera, written in any spelling.
    config : `AppConfiguration`, optional
        The config to read camera profiles from. The process-wide
        singleton is used when this is left out.

    Returns
    -------
    camera_names : `list` [`str`]
        The cameras, each spelled the way frame records spell it (the
        profile's ``record_name``), so that they match the cameras stored on
        frames. Empty when the config lists no setups.
    """
    names_by_identity: dict[str, str] = {}
    for setup in observatory_setups.setups:
        names_by_identity.setdefault(
            camera_identity(setup.camera_name, config), record_name_for_camera(setup.camera_name, config)
        )

    primary_identity = camera_identity(primary_camera_name, config) if primary_camera_name else None
    ordered_identities = list(names_by_identity)
    if primary_identity in names_by_identity:
        ordered_identities.remove(primary_identity)
        ordered_identities.insert(0, primary_identity)
    return [names_by_identity[identity] for identity in ordered_identities]


def assign_targets_to_cameras(targets: list[Any], camera_names: list[str]) -> dict[str, list[str]]:
    """Give each target to the first camera that has frames of it.

    Parameters
    ----------
    targets : `list`
        The targets to divide up.
    camera_names : `list` [`str`]
        The cameras, best first, as returned by `camera_pass_order`.

    Returns
    -------
    assignments : `dict` [`str`, `list` [`str`]]
        For each camera, the ids of the targets it should process, in the
        order the targets were given. A target with frames from none of the
        cameras is in no list.
    """
    assignments: dict[str, list[str]] = {camera_name: [] for camera_name in camera_names}
    for target in targets:
        for camera_name in camera_names:
            if select_frames_for_camera(target, camera_name):
                assignments[camera_name].append(target.id)
                break
    return assignments


@dataclass(frozen=True)
class ProcessingFrameSelection:
    """The frames one processing run works on, and how they were chosen.

    Attributes
    ----------
    camera_name : `str` or `None`
        The camera the frames came from. `None` when no camera could be
        chosen, or when no camera filter was applied.
    focal_length_mm : `float` or `None`
        The optic the frames came from, in millimeters. `None` when no
        focal length filter was applied.
    frames : `list` [`FrameRecord`]
        Every selected frame, imaging and spectral. Empty when nothing
        matched.
    standard_frames : `list` [`FrameRecord`]
        The selected imaging (non-spectral) frames, without stacked or
        starless products.
    spectral_frames : `list` [`FrameRecord`]
        The selected spectral frames.
    stack_path : `str` or `None`
        The imaging stack the caller named as the one being analyzed, when
        it is not the target's recorded stack. `None` means the target's
        recorded stack.
    """

    camera_name: str | None
    focal_length_mm: float | None
    frames: list[FrameRecord]
    standard_frames: list[FrameRecord]
    spectral_frames: list[FrameRecord]
    stack_path: str | None = None


def resolve_processing_frames(
    target: Any,
    config: Any,
    camera_name: str | None = None,
    focal_length_mm: float | None = None,
    stack_path: str | None = None,
) -> ProcessingFrameSelection:
    """Choose the frames a single-target run works on, the way the batch does.

    When the target has a stack (or the caller names the stack being
    analyzed), the frames come from the camera and optic of that stack
    (its header's ``INSTRUME`` and ``FOCALLEN``, or the target's stack
    record), because the analysis measures that stack and
    leaves out frames of any other camera or optic. A target imaged only
    with a 300 mm lens is analyzed at 300 mm, even when the primary optic is
    405 mm. Without a stack, the choices are the batch script's: the first
    camera (in `camera_pass_order`) that has frames of the target, and the
    primary optic. Any choice the caller makes is used instead.

    Without observatory setups in the config there is no camera order, so no
    camera filter is applied and a warning is logged. Without a primary
    focal length, no focal length filter is applied.

    Parameters
    ----------
    target : `Any`
        The target whose frames to choose from.
    config : `AppConfiguration`
        The config that gives the setups, primary camera and primary optic.
    camera_name : `str`, optional
        Use this camera (the full name, or part of it). By default, the
        camera of the target's stack, or else the camera the batch would
        give the target to.
    focal_length_mm : `float`, optional
        Use this optic. By default, the optic of the target's stack when it
        was taken with the chosen camera, or else the config's primary focal
        length.
    stack_path : `str`, optional
        The imaging stack being analyzed, when it is not the target's
        recorded stack (for example another configuration's stack). Its
        camera and optic are used instead of the recorded stack's.

    Returns
    -------
    selection : `ProcessingFrameSelection`
        The camera, the optic and the frames. The frame lists are empty when
        nothing matches.
    """
    analyzed_stack = (
        _analyzed_stack_configuration(target, stack_path)
        if not camera_name or focal_length_mm is None
        else None
    )
    if not camera_name and analyzed_stack is not None:
        camera_name = record_name_for_camera(analyzed_stack.camera, config)
    if not camera_name:
        camera_names = camera_pass_order(
            config.get_observatory_setups(), config.get_primary_camera_name(), config
        )
        if not camera_names:
            logger.warning(
                "[%s] The config lists no [Observatory.Setups], so frames of every camera are used.",
                target.id,
            )
            camera_name = ""
        else:
            assignments = assign_targets_to_cameras([target], camera_names)
            camera_name = next((name for name in camera_names if assignments[name]), None)
            if camera_name is None:
                logger.warning(
                    "[%s] No frames from any configured camera (%s); nothing to process.",
                    target.id,
                    ", ".join(camera_names),
                )
                return ProcessingFrameSelection(None, focal_length_mm, [], [], [], stack_path)
    if focal_length_mm is None and analyzed_stack is not None and analyzed_stack.focal_length_mm:
        if _same_camera(camera_name, analyzed_stack.camera, config):
            focal_length_mm = float(analyzed_stack.focal_length_mm)
    if focal_length_mm is None:
        focal_length_mm = config.get_primary_focal_length_mm()

    # An empty camera name matches every frame (substring match).
    frames = select_frames_for_processing(target, camera_name, focal_length_mm) or []
    standard_frames, spectral_frames = split_standard_and_spectral_frames(target, frames)
    return ProcessingFrameSelection(
        camera_name or None, focal_length_mm, frames, standard_frames, spectral_frames, stack_path
    )


def _analyzed_stack_configuration(target: Any, stack_path: str | None = None) -> AnalysisConfiguration | None:
    """Read the camera and optic of the stack an analysis of the target reads.

    The stack the caller names is used when given. Otherwise the imaging
    stack is used when there is one, else the spectral stack.

    Parameters
    ----------
    target : `Any`
        The target. A stand-in without stack records has no stack.
    stack_path : `str`, optional
        The stack being analyzed, when the caller names one.

    Returns
    -------
    configuration : `AnalysisConfiguration` or `None`
        The stack's camera and optic, or `None` when the target has no stack
        that names a camera.
    """
    if stack_path:
        named = stack_configuration(stack_path, target)
        if named is not None:
            return named
    for stacking_name in ("stacking", "spectral_stacking"):
        stacking = getattr(target, stacking_name, None)
        stack_path = getattr(stacking, "stacked_image", None)
        if not stack_path:
            continue
        configuration = stack_configuration(stack_path, target)
        if configuration is not None:
            return configuration
    return None


def _same_camera(camera_name: str, stack_camera: str, config: Any) -> bool:
    """Say whether the chosen camera is the camera a stack was taken with.

    Parameters
    ----------
    camera_name : `str`
        The chosen camera: a full name, part of one, or empty for any camera.
    stack_camera : `str`
        The camera the stack's header or record names.
    config : `AppConfiguration`
        The config whose camera profiles give the aliases.

    Returns
    -------
    same : `bool`
        `True` when the names are aliases of one camera, when the chosen name
        is part of the stack camera's name, or when no camera was chosen.
    """
    if not camera_name:
        return True
    if camera_identity(camera_name, config) == camera_identity(stack_camera, config):
        return True
    wanted = camera_name.lower()
    return wanted in stack_camera.lower() or wanted in record_name_for_camera(stack_camera, config).lower()
