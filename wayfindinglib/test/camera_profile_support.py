"""Purpose: Stand-ins for the science library's camera-profile call.

Description: Code in this library reads a camera's profile with
``astrometrics.processing.calibration.camera_profile(name)``. These helpers
build the small ``processing`` object a test library stand-in needs, so a test
can say which configuration holds the profiles (`camera_profile_processing`)
or that none exist (`no_camera_profiles_processing`).
"""

from types import SimpleNamespace
from typing import Any

from astrometricslib._internal.frames import resolve_camera_profile
from astrometricslib.exceptions import ConfigurationError

__all__ = ["camera_profile_processing", "library_with_camera_profiles", "no_camera_profiles_processing"]


def camera_profile_processing(config: Any) -> SimpleNamespace:
    """Build a ``processing`` stand-in that reads profiles of a configuration.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration that holds the camera profiles.

    Returns
    -------
    processing : `types.SimpleNamespace`
        An object whose ``calibration.camera_profile`` gives what the
        library's call gives for that configuration.
    """
    return SimpleNamespace(
        calibration=SimpleNamespace(camera_profile=lambda name: resolve_camera_profile(name, config))
    )


def no_camera_profiles_processing() -> SimpleNamespace:
    """Build a ``processing`` stand-in for a configuration with no profiles.

    Returns
    -------
    processing : `types.SimpleNamespace`
        An object whose ``calibration.camera_profile`` raises
        `ConfigurationError`, as the library does when the configuration holds
        no camera profiles.
    """

    def camera_profile(name: str | None) -> Any:
        """Refuse, as a configuration with no camera profiles does.

        Raises
        ------
        ConfigurationError
            Always.
        """
        raise ConfigurationError(f"no camera profiles are configured (asked for {name!r})")

    return SimpleNamespace(calibration=SimpleNamespace(camera_profile=camera_profile))


def library_with_camera_profiles(config: Any) -> SimpleNamespace:
    """Build an ``Astrometrics`` stand-in that holds camera profiles only.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration that holds the camera profiles.

    Returns
    -------
    library : `types.SimpleNamespace`
        An object with ``config`` and a ``processing`` that answers
        ``calibration.camera_profile`` from that configuration.
    """
    return SimpleNamespace(config=config, processing=camera_profile_processing(config))
