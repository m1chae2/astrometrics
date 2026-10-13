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

from astrometricslib.drivers.camera_profile_store import camera_identity, record_name_for_camera
from astrometricslib.foundation.observatory_setups import ObservatorySetups
from astrometricslib.models.target import FrameRecord
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
    """

    camera_name: str | None
    focal_length_mm: float | None
    frames: list[FrameRecord]
    standard_frames: list[FrameRecord]
    spectral_frames: list[FrameRecord]


def resolve_processing_frames(
    target: Any,
    config: Any,
    camera_name: str | None = None,
    focal_length_mm: float | None = None,
) -> ProcessingFrameSelection:
    """Choose the frames a single-target run works on, the way the batch does.

    The batch script gives each target to the first camera (in
    `camera_pass_order`) that has frames of it, and keeps only the frames
    at the primary optic. This function makes the same choices for one
    target, so a single-target run measures the same frames. Any choice the
    caller makes is used instead.

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
        camera the batch would give the target to.
    focal_length_mm : `float`, optional
        Use this optic. By default, the config's primary focal length.

    Returns
    -------
    selection : `ProcessingFrameSelection`
        The camera, the optic and the frames. The frame lists are empty when
        nothing matches.
    """
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
                return ProcessingFrameSelection(None, focal_length_mm, [], [], [])
    if focal_length_mm is None:
        focal_length_mm = config.get_primary_focal_length_mm()

    # An empty camera name matches every frame (substring match).
    frames = select_frames_for_processing(target, camera_name, focal_length_mm) or []
    standard_frames, spectral_frames = split_standard_and_spectral_frames(target, frames)
    return ProcessingFrameSelection(
        camera_name or None, focal_length_mm, frames, standard_frames, spectral_frames
    )
