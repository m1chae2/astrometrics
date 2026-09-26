"""Plan which cameras a batch run processes, and which targets each one gets.

A target record holds one stack and one set of quality summaries, not one per
camera. If two cameras were both processed for the same target, the second
would overwrite the first. So each target is handed to a single camera: the
first one, in priority order, that has frames of it.

The cameras and their order come from the setups in the config file (see
`astrometricslib.utilities.observatory_setups`), not from names written into
a script.
"""

from typing import Any

from astrometricslib.drivers.camera_profile_store import camera_identity, record_name_for_camera
from astrometricslib.pipelines.shared.frame_grouping import select_frames_for_camera
from astrometricslib.utilities.observatory_setups import ObservatorySetups


def camera_pass_order(observatory_setups: ObservatorySetups, primary_camera_name: str | None) -> list[str]:
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
            camera_identity(setup.camera_name), record_name_for_camera(setup.camera_name)
        )

    primary_identity = camera_identity(primary_camera_name) if primary_camera_name else None
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
