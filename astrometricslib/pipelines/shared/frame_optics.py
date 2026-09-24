"""Work out which optic (telescope or lens) took a frame.

The answer is used as the frame's telescope name. That name decides which flat
frames are used and which library folder the frame is kept in, so a wrong
answer can mean the wrong calibration.

Which cameras are used with which optics is not guessed here. It comes from the
setups the observer lists in the config file (see
`astrometricslib.utilities.observatory_setups`).
"""

import logging
from dataclasses import dataclass

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.utilities.camera_names import normalize_camera_name
from astrometricslib.utilities.observatory_setups import ObservatorySetups, OpticConfig
from astrometricslib.utilities.warn_once import warn_once

logger = logging.getLogger(__name__)

# The telescope name given to a frame when its optic cannot be worked out.
UNKNOWN_TELESCOPE_NAME = "Unknown"

# A frame's focal length matches an optic when they differ by no more than this
# fraction. The optics in this library are 300 mm and 405 mm, 35% apart, so a
# tolerance of 2% cannot mix them up. It only has to allow for a focal length
# that was rounded, or typed slightly differently in the config. It was not
# tuned on any data.
FOCAL_LENGTH_TOLERANCE_FRACTION = 0.02

REASON_FOCAL_LENGTH = "focal_length"
REASON_ONLY_SETUP = "only_setup_for_camera"
REASON_PATH_NAME = "optic_name_in_path"
REASON_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class TelescopeResolution:
    """The optic chosen for one frame, and why.

    Attributes
    ----------
    telescope_name : `str`
        The optic's name, or `UNKNOWN_TELESCOPE_NAME`.
    reason : `str`
        Which rule decided it: ``focal_length``, ``only_setup_for_camera``,
        ``optic_name_in_path`` or ``unresolved``.
    """

    telescope_name: str
    reason: str


def camera_identity(camera_name: str) -> str:
    """Reduce a camera name to text that is the same for every spelling of it.

    Parameters
    ----------
    camera_name : `str`
        A camera name, for example from a header or from the config.

    Returns
    -------
    identity : `str`
        The camera's profile name when it has a profile, otherwise the name
        with case, spaces and punctuation removed.
    """
    profile = resolve_camera_profile(camera_name)
    if profile.is_generic_fallback:
        return normalize_camera_name(camera_name)
    return normalize_camera_name(profile.camera_name)


def _warn_once_unresolved(camera_name: str | None, focal_length_mm: float | None, reason: str) -> None:
    """Log that a frame's optic could not be worked out, once per case.

    Parameters
    ----------
    camera_name : `str` or `None`
        The frame's camera.
    focal_length_mm : `float` or `None`
        The frame's focal length, when it has one.
    reason : `str`
        Why the optic could not be chosen, in plain words.
    """
    warn_once(
        logger,
        f"Could not tell which optic took frames from camera {camera_name!r} "
        f"(focal length {focal_length_mm}): {reason}. Their telescope name is "
        f"{UNKNOWN_TELESCOPE_NAME!r}. List the setup under [Observatory.Setups] in the config.",
    )


def resolve_frame_telescope(
    camera_name: str | None,
    focal_length_mm: float | None,
    path: str | None,
    observatory_setups: ObservatorySetups,
) -> TelescopeResolution:
    """Choose the optic that took a frame.

    The rules are tried in this order, and only the setups that use this
    frame's camera are considered:

    1. If the frame records a focal length, use the optic whose focal length
       is within `FOCAL_LENGTH_TOLERANCE_FRACTION` of it. If none is close
       enough the frame is `UNKNOWN_TELESCOPE_NAME`.
    2. If the frame has no focal length and the camera has exactly one setup,
       use that setup's optic.
    3. Otherwise, if exactly one of those optics has its name in the file
       path, use it. This is the older rule, kept for frames that carry no
       focal length.
    4. Otherwise the frame is `UNKNOWN_TELESCOPE_NAME`, with one warning per
       camera and focal length.

    Parameters
    ----------
    camera_name : `str` or `None`
        The frame's camera, written in any spelling.
    focal_length_mm : `float` or `None`
        The focal length recorded in the frame's header, when there is one.
    path : `str` or `None`
        The frame's file path.
    observatory_setups : `ObservatorySetups`
        The optics and setups from the config.

    Returns
    -------
    resolution : `TelescopeResolution`
        The chosen optic name and the rule that chose it.
    """
    if not camera_name:
        return TelescopeResolution(UNKNOWN_TELESCOPE_NAME, REASON_UNRESOLVED)

    frame_camera_identity = camera_identity(camera_name)
    candidate_optics: list[OpticConfig] = []
    for setup in observatory_setups.setups:
        if camera_identity(setup.camera_name) != frame_camera_identity:
            continue
        optic = observatory_setups.optic_named(setup.optic_name)
        if optic is not None and optic not in candidate_optics:
            candidate_optics.append(optic)

    if not candidate_optics:
        _warn_once_unresolved(camera_name, focal_length_mm, "no setup in the config uses this camera")
        return TelescopeResolution(UNKNOWN_TELESCOPE_NAME, REASON_UNRESOLVED)

    if focal_length_mm is not None and focal_length_mm > 0:
        close_enough = [
            optic
            for optic in candidate_optics
            if abs(focal_length_mm - optic.focal_length_mm) / optic.focal_length_mm
            <= FOCAL_LENGTH_TOLERANCE_FRACTION
        ]
        if close_enough:
            closest = min(close_enough, key=lambda optic: abs(focal_length_mm - optic.focal_length_mm))
            return TelescopeResolution(closest.name, REASON_FOCAL_LENGTH)
        _warn_once_unresolved(
            camera_name, focal_length_mm, "no optic paired with this camera has that focal length"
        )
        return TelescopeResolution(UNKNOWN_TELESCOPE_NAME, REASON_UNRESOLVED)

    if len(candidate_optics) == 1:
        return TelescopeResolution(candidate_optics[0].name, REASON_ONLY_SETUP)

    named_in_path = [optic for optic in candidate_optics if path and optic.name in path]
    if len(named_in_path) == 1:
        return TelescopeResolution(named_in_path[0].name, REASON_PATH_NAME)

    _warn_once_unresolved(
        camera_name,
        focal_length_mm,
        "the frame has no focal length and its path does not single out one optic",
    )
    return TelescopeResolution(UNKNOWN_TELESCOPE_NAME, REASON_UNRESOLVED)
