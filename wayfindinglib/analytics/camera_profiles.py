"""Purpose: Find a camera's noise profile from a camera name in any spelling.

Description: The science library gives a camera's noise profile for a
`Camera` (``astrometrics.calibration.get_profile(camera)``). Code here often
holds only a name: the camera a frame header, a stored capture setting or a
plan names, written in any of the spellings a camera has (``ZWO CCD
ASI533MM Pro`` and ``ZWO ASI 533MM Pro`` are one camera). `find_camera`
turns such a name into the camera the settings list: first by the camera's
own name and aliases, then by the names its profile lists. `profile_for_name`
gives that camera's profile. A name the settings list no camera for (an old
camera that has a profile section but no sensor size, or a header spelling)
is looked up as a camera known only by that name, so its profile is still
found by any spelling the profile lists; an unknown name gives the generic
fallback profile.

Names are compared without case, spaces or punctuation.
"""

import re
from typing import Any

from astrometricslib import Camera

__all__ = ["find_camera", "profile_for_name"]


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


def find_camera(astrometrics: Any, name: str | None) -> Any:
    """Find the listed camera a name means.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle. Its settings list the cameras.
    name : `str` or `None`
        The camera's name, in any spelling.

    Returns
    -------
    camera : `Camera` or `None`
        The camera whose name or alias is ``name``, else the camera whose
        profile lists ``name``, else `None`.

    Notes
    -----
    Reading a profile can raise `ConfigurationError` when the settings hold
    no valid camera profiles; the error is passed on.
    """
    wanted = _spelling(name)
    if not wanted:
        return None
    cameras = list(astrometrics.config.get_cameras())
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


def profile_for_name(astrometrics: Any, name: str | None) -> Any:
    """Give the noise profile of the camera a name means.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    name : `str` or `None`
        The camera's name, in any spelling.

    Returns
    -------
    profile : `CameraProfile`
        The camera's profile, or the generic fallback profile (its
        ``is_generic_fallback`` is `True`) when no profile lists the name.

    Notes
    -----
    Raises `ConfigurationError` when the settings hold no valid camera
    profiles.
    """
    camera = find_camera(astrometrics, name)
    if camera is None and name and name.strip():
        # A camera known only by its name: the profile lookup reads nothing
        # but the name and the aliases, so no sensor size is made up.
        camera = Camera.model_construct(id=name, name=name, aliases=())
    return astrometrics.calibration.get_profile(camera)
