"""Purpose: Stand-ins for the science library's camera-profile calls.

Description: Code in this library finds a camera's profile from a camera
name (`wayfindinglib.analytics.camera_profiles.profile_for_name`), which
reads the cameras the settings list (``astrometrics.config.get_cameras()``)
and asks ``astrometrics.calibration.get_profile(camera)``. These helpers
build the small parts a test library stand-in needs, so a test can say which
configuration holds the profiles (`camera_profile_calibration`,
`library_with_camera_profiles`) or that none exist
(`no_camera_profiles_calibration`, `NO_CAMERAS`).
"""

from types import SimpleNamespace
from typing import Any

from astrometricslib._internal.frames import resolve_camera_profile
from astrometricslib.exceptions import ConfigurationError

__all__ = [
    "NO_CAMERAS",
    "camera_profile_calibration",
    "library_with_camera_profiles",
    "no_camera_profiles_calibration",
]

NO_CAMERAS = SimpleNamespace(get_cameras=list)
"""A ``config`` stand-in whose settings list no camera."""


def camera_profile_calibration(config: Any) -> SimpleNamespace:
    """Build a ``calibration`` stand-in that reads profiles of a configuration.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration that holds the camera profiles.

    Returns
    -------
    calibration : `types.SimpleNamespace`
        An object whose ``get_profile`` gives what the library's call gives
        for that configuration.
    """

    def get_profile(camera: Any = None) -> Any:
        """Give the profile of the camera's name, as the library does.

        Returns
        -------
        profile : `CameraProfile`
            The camera's profile, or the generic fallback.
        """
        return resolve_camera_profile(None if camera is None else camera.name, config)

    return SimpleNamespace(get_profile=get_profile)


def no_camera_profiles_calibration() -> SimpleNamespace:
    """Build a ``calibration`` stand-in for a configuration with no profiles.

    Returns
    -------
    calibration : `types.SimpleNamespace`
        An object whose ``get_profile`` raises `ConfigurationError`, as the
        library does when the configuration holds no camera profiles.
    """

    def get_profile(camera: Any = None) -> Any:
        """Refuse, as a configuration with no camera profiles does.

        Raises
        ------
        ConfigurationError
            Always.
        """
        raise ConfigurationError(f"no camera profiles are configured (asked for {camera!r})")

    return SimpleNamespace(get_profile=get_profile)


def library_with_camera_profiles(config: Any) -> SimpleNamespace:
    """Build an ``Astrometrics`` stand-in that holds camera profiles only.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration that lists the cameras and holds their profiles.

    Returns
    -------
    library : `types.SimpleNamespace`
        An object with ``config`` and a ``calibration`` that answers
        ``get_profile`` from that configuration.
    """
    return SimpleNamespace(config=config, calibration=camera_profile_calibration(config))
