"""Purpose: Read the camera facts the fit needs from the world's settings file.

Description: Converting a measurement into a simulator setting needs a few
facts about the simulated cameras: the frame size, the gain the simulator runs
at, the bias level the frames must sit at, the read noise and the clip
ceiling. They come from the camera profiles in the world's settings file (a
copy of the real settings), read the way `simulator.device_settings` reads
them, so the fit and the geometry settings agree. Nothing here talks to a
server or to the library.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator import device_settings
from simulator.fit.driver_model import GUIDE_DEFAULT_GAIN

__all__ = ["CameraSetup", "WorldSetup", "world_setup_from_settings"]

#: The INDI simulators' default limiting and saturation magnitudes
#: (``ccd_simulator.cpp:133-134``, ``guide_simulator.cpp:179-180``). The
#: world never changes them, and the sky and star flux formulas use them.
DEFAULT_LIMITING_MAGNITUDE = 17.0
DEFAULT_SATURATION_MAGNITUDE = 1.0


@dataclass(frozen=True)
class CameraSetup:
    """What the fit knows about one simulated camera.

    Attributes
    ----------
    width_px, height_px : `int` or `None`
        The frame size in pixels, or `None` when the profile has none.
    gain : `float`
        The gain the simulator runs at (``CCD_GAIN``).
    bias_level_adu : `float` or `None`
        The real camera's bias level at that setting, in ADU.
    read_noise_adu : `float` or `None`
        The real camera's read noise at that setting (RMS), in ADU.
    gain_e_per_adu : `float` or `None`
        The real camera's conversion gain at that setting, in electrons per
        ADU. The closure check needs it to turn the measured sky rate in
        electrons into ADU.
    clip_ceiling_adu : `float` or `None`
        The largest value the real camera writes.
    """

    width_px: int | None
    height_px: int | None
    gain: float
    bias_level_adu: float | None
    read_noise_adu: float | None
    gain_e_per_adu: float | None
    clip_ceiling_adu: float | None


@dataclass(frozen=True)
class WorldSetup:
    """The simulated cameras and where the telescope points.

    Attributes
    ----------
    main, guide : `CameraSetup`
        The main and guide cameras.
    declination_degrees : `float`
        The declination of the simulated target.
    guide_pixel_scale_arcsec : `float` or `None`
        The guide camera's pixel scale, in arcseconds per pixel (pixel size
        over the active guide scope's focal length). `None` when either is
        not in the settings.
    """

    main: CameraSetup
    guide: CameraSetup
    declination_degrees: float
    guide_pixel_scale_arcsec: float | None = None


def _value(entry: Any) -> float | None:
    """Read a number that may carry its source (``{value, kind, source}``).

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when it is missing or not a number.
    """
    raw = entry["value"] if isinstance(entry, Mapping) and "value" in entry else entry
    try:
        return float(raw)
    except TypeError, ValueError:
        return None


def _matching_noise_entry(profile: Mapping[str, Any], gain: float, offset: float) -> Mapping[str, Any]:
    """Find the sensor noise entry for a gain and offset.

    Returns
    -------
    entry : `~collections.abc.Mapping`
        The ``sensor_noise`` entry for this setting, else the first entry,
        else an empty mapping.
    """
    entries = profile.get("sensor_noise") or []
    for entry in entries:
        try:
            if math.isclose(float(entry["gain_setting"]), gain) and math.isclose(
                float(entry["offset"]), offset
            ):
                return entry
        except KeyError, TypeError, ValueError:
            continue
    return entries[0] if entries else {}


def _camera_setup(profile: Mapping[str, Any], default_gain: float | None) -> CameraSetup:
    """Read one camera's facts from its profile.

    Parameters
    ----------
    profile : `~collections.abc.Mapping`
        The ``Observatory.Camera.<name>`` section (empty when there is none).
    default_gain : `float` or `None`
        The gain to use when the profile records no capture setting.

    Returns
    -------
    setup : `CameraSetup`
        The facts the profile gives.
    """
    capture = device_settings._capture_setting(profile)
    gain, offset = capture if capture is not None else (default_gain, None)
    bias = None
    noise_adu = None
    gain_e = None
    if capture is not None:
        bias = device_settings._bias_level_adu(profile, gain, offset)
        entry = _matching_noise_entry(profile, gain, offset)
        gain_e = _value(entry.get("gain_e_per_adu"))
        read_noise_e = _value(entry.get("read_noise_e"))
        if gain_e and read_noise_e:
            noise_adu = read_noise_e / gain_e
        else:
            noise_adu = _value(entry.get("read_noise_adu_robust"))
    width = device_settings._number(profile.get("sensor_width_px"))
    height = device_settings._number(profile.get("sensor_height_px"))
    return CameraSetup(
        width_px=None if width is None else int(width),
        height_px=None if height is None else int(height),
        gain=float(gain) if gain is not None else GUIDE_DEFAULT_GAIN,
        bias_level_adu=bias,
        read_noise_adu=noise_adu,
        gain_e_per_adu=gain_e,
        clip_ceiling_adu=_value(profile.get("clip_ceiling_adu")),
    )


def world_setup_from_settings(settings: Mapping[str, Any], declination_degrees: float) -> WorldSetup:
    """Read the simulated cameras from the world's settings.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings file of the world.
    declination_degrees : `float`
        The simulated target's declination.

    Returns
    -------
    setup : `WorldSetup`
        The main camera (the primary camera's profile) and the guide camera
        (the secondary camera's profile). The guide camera runs at INDI's
        default gain, because the world sets none.
    """
    primary = device_settings._camera_profile(settings, "default_primary_camera")
    secondary = device_settings._camera_profile(settings, "default_secondary_camera")
    return WorldSetup(
        main=_camera_setup(primary, None),
        guide=_camera_setup(secondary, GUIDE_DEFAULT_GAIN),
        declination_degrees=declination_degrees,
        guide_pixel_scale_arcsec=device_settings.guide_pixel_scale(settings),
    )
