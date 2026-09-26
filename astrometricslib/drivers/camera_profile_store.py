"""Finds and loads the stored profile for a camera.

The profiles are JSON files in ``astrometricslib/instruments/cameras/``,
one per camera model (see `astrometricslib.models.camera_profile` for what
each file holds). This module reads them and picks the right one for a
camera name taken from a FITS header, a frame record or a config file.

A camera that has no profile gets the generic fallback profile instead of
an error, so a new camera does not stop processing. A warning is logged
the first time each unlisted name is seen, so the gap is not silent.
"""

import functools
import logging
from pathlib import Path

from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.utilities.camera_names import normalize_camera_name

logger = logging.getLogger(__name__)

CAMERA_PROFILE_DIRECTORY = Path(__file__).resolve().parent.parent / "instruments" / "cameras"


@functools.cache
def _load_profiles_from_directory(directory: Path) -> tuple[CameraProfile, ...]:
    """Read and check every camera profile file in a folder.

    Parameters
    ----------
    directory : `pathlib.Path`
        The folder holding the ``*.json`` profile files.

    Returns
    -------
    profiles : `tuple` [`CameraProfile`, ...]
        Every profile found, in file name order. The result is cached, so
        each folder is read from disk only once per run.

    Raises
    ------
    ValueError
        If the folder does not hold exactly one generic fallback
        profile, or if two profiles claim the same camera name.
    """
    profiles = tuple(
        CameraProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
        for profile_path in sorted(directory.glob("*.json"))
    )

    fallback_count = sum(1 for profile in profiles if profile.is_generic_fallback)
    if fallback_count != 1:
        raise ValueError(
            f"{directory} must hold exactly one generic fallback profile, found {fallback_count}"
        )

    owner_by_normalized_name: dict[str, str] = {}
    for profile in profiles:
        if profile.is_generic_fallback:
            continue
        for name in (profile.camera_name, *profile.name_aliases):
            normalized_name = normalize_camera_name(name)
            owner = owner_by_normalized_name.setdefault(normalized_name, profile.camera_name)
            if owner != profile.camera_name:
                raise ValueError(
                    f"the name {name!r} is claimed by both {owner!r} and {profile.camera_name!r}"
                )
    return profiles


def load_camera_profiles(directory: Path | None = None) -> tuple[CameraProfile, ...]:
    """Return every stored camera profile, including the generic fallback.

    Parameters
    ----------
    directory : `pathlib.Path`, optional
        The folder to read. The repository's own profile folder is used
        when this is left out.

    Returns
    -------
    profiles : `tuple` [`CameraProfile`, ...]
        The profiles, in file name order.
    """
    return _load_profiles_from_directory(directory or CAMERA_PROFILE_DIRECTORY)


@functools.cache
def _warn_once_about_unlisted_camera(camera_name: str) -> None:
    """Log a warning the first time a camera name has no profile.

    Parameters
    ----------
    camera_name : `str`
        The camera name that matched no profile. Because the result is
        cached, a second call with the same name does nothing.
    """
    logger.warning(
        "No camera profile matches %r; using the generic profile. Add a file for this camera to %s.",
        camera_name,
        CAMERA_PROFILE_DIRECTORY,
    )


def resolve_camera_profile(camera_name: str | None, directory: Path | None = None) -> CameraProfile:
    """Pick the profile that matches a camera name.

    Parameters
    ----------
    camera_name : `str` or `None`
        The camera name, written in any spelling. Case, spaces and
        punctuation are ignored when names are compared.
    directory : `pathlib.Path`, optional
        The folder to read. The repository's own profile folder is used
        when this is left out.

    Returns
    -------
    profile : `CameraProfile`
        The matching profile. When nothing matches, or no name is given,
        this is the generic fallback profile, whose ``is_generic_fallback``
        is `True`. A name that is given but matches nothing also logs a
        warning, once per distinct name.
    """
    profiles = load_camera_profiles(directory)
    generic_profile = next(profile for profile in profiles if profile.is_generic_fallback)

    if not camera_name or not camera_name.strip():
        return generic_profile

    wanted_name = normalize_camera_name(camera_name)
    for profile in profiles:
        if profile.is_generic_fallback:
            continue
        known_names = (profile.camera_name, *profile.name_aliases)
        if any(normalize_camera_name(known_name) == wanted_name for known_name in known_names):
            return profile

    _warn_once_about_unlisted_camera(camera_name)
    return generic_profile


def camera_identity(camera_name: str) -> str:
    """Reduce a camera name to text that is the same for every spelling of it.

    Two names give the same identity when they are the same camera, whether
    they differ in case, spaces and punctuation or are listed as aliases in the
    camera's profile (for example ``ZWO CCD ASI533MM Pro`` and
    ``ZWO ASI 533MM Pro``).

    Parameters
    ----------
    camera_name : `str`
        A camera name, for example from a header or from the config.

    Returns
    -------
    identity : `str`
        The camera's profile name, reduced to lowercase letters and digits,
        when it has a profile. Otherwise the name itself reduced the same way.
    """
    profile = resolve_camera_profile(camera_name)
    if profile.is_generic_fallback:
        return normalize_camera_name(camera_name)
    return normalize_camera_name(profile.camera_name)


def record_name_for_camera(camera_name: str) -> str:
    """Give the spelling of a camera's name that frame records use.

    Parameters
    ----------
    camera_name : `str`
        The camera's name as written in an image header.

    Returns
    -------
    record_name : `str`
        The profile's ``record_name`` when the camera has a profile that sets
        one, otherwise `camera_name` unchanged.
    """
    profile = resolve_camera_profile(camera_name)
    return profile.record_name or camera_name
