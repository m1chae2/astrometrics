"""Purpose: Turn the world's settings into INDI simulator properties.

Description: The INDI simulators start with made-up equipment: a 1280 x 1024
camera with 5.2 micron pixels and no telescope focal length. This module reads
the settings file of the world (a copy of the real settings, see
`simulator.observatory`) and lists the INDI properties that make the
simulators match the real equipment's geometry (they do not make the frames
realistic: the sky brightness, noise, star width and star brightness stay at
INDI's defaults, so noise, signal-to-noise and depth checks are not valid on
the frames until the fitted settings of step S3 of the simulator plan):

* the main camera simulator's resolution and pixel size (``SIM_XRES``,
  ``SIM_YRES``, ``SIM_XSIZE``, ``SIM_YSIZE``), from the primary camera's
  profile;
* the guide camera simulator's, from the secondary camera's profile;
* the focal length and aperture both camera simulators use to compute the
  field scale (``SCOPE_INFO``), from ``Observatory.Telescope`` (main camera)
  and the active guide scope (guide camera);
* the filter wheel simulator's slot names (``FILTER_NAME``), from
  ``[Observatory.Filters]`` (``available``, else the older ``owned``), and the
  camera simulator's link to that wheel (``ACTIVE_FILTER``), so the frames'
  ``FILTER`` header reads the real names;
* the main camera's cooler target (``CCD_TEMPERATURE``), from the temperature
  recorded with the primary camera profile's bias level (else its noise
  measurement);
* the main camera's gain (``CCD_GAIN``) and pedestal (``CCD_OFFSET``), from
  the first bias level (or noise measurement) recorded in the primary
  camera's profile. The INDI simulator uses ``CCD_OFFSET`` as the pixel
  value of an empty frame, not as a camera offset setting, so the value sent
  is the real camera's bias level (in ADU) for that setting. The camera
  offset setting itself is kept in `DeviceSetting.header_value` and written
  into the frame's ``OFFSET`` header.

Nothing here talks to a server. `exposure_driver.ExposureDriver` sends the
properties after it connects the simulators. The property names were checked
against the INDI 2.2.5 simulators (`indi_simulator_ccd` and
`indi_simulator_guide`).
The telescope simulator has no focal-length property in that version; the
camera simulators read their own ``SCOPE_INFO``.

The filter wheel simulator has 8 slots that cannot change, so a longer list
is cut at 8 and a shorter one leaves the later slots with the simulator's own
names. The camera simulator has no cooler switch (no ``CCD_COOLER``); setting
``CCD_TEMPERATURE`` starts the cooling, and the value read back is the current
temperature, not the target, so that setting is not read back.

The INDI driver does not write an ``OFFSET`` header into its frames (it
writes ``GAIN``), so `simulator.exposure_driver` adds it from
`DeviceSetting.header_value`.
"""

import logging
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
MAIN_CAMERA = "CCD Simulator"
GUIDE_CAMERA = "Guide Simulator"
#: The focal length (mm) and focal ratio used when the settings give none.
FALLBACK_FOCAL_LENGTH_MM = 405.0
FALLBACK_FOCAL_RATIO = 5.4
#: How close a read-back value must be to the value that was set, relative.
READ_BACK_TOLERANCE = 1e-3


@dataclass(frozen=True)
class DeviceSetting:
    """One INDI property to set, with its elements.

    Parameters
    ----------
    device : `str`
        The INDI device name, for example ``CCD Simulator``.
    property_name : `str`
        The property name, for example ``SIMULATOR_SETTINGS``.
    elements : `tuple` [`tuple` [`str`, `float` or `str`], ...]
        ``(element name, value)`` pairs.
    check : `bool`, optional
        `True` (default) to read the values back after setting. `False` for a
        property whose value moves on its own (the cooler's temperature).
    header_value : `float` or `None`, optional
        The value to write in the frame header when it differs from the value
        sent. Used by ``CCD_OFFSET``: the simulator is sent a pedestal in ADU,
        while the header keeps the camera's offset setting.
    """

    device: str
    property_name: str
    elements: tuple[tuple[str, float | str], ...]
    check: bool = True
    header_value: float | None = None

    def specification(self) -> str:
        """Write the setting the way ``indi_setprop`` takes it.

        Returns
        -------
        specification : `str`
            ``device.property.e1;e2=v1;v2``.
        """
        names = ";".join(name for name, _ in self.elements)
        values = ";".join(
            value if isinstance(value, str) else format(value, ".10g") for _, value in self.elements
        )
        return f"{self.device}.{self.property_name}.{names}={values}"

    def read_back_names(self) -> list[tuple[str, float | str]]:
        """List what to read back to check the setting took.

        Returns
        -------
        names : `list` [`tuple` [`str`, `float` or `str`]]
            ``(device.property.element, expected value)`` pairs. Empty when
            the setting is not checked.
        """
        if not self.check:
            return []
        return [(f"{self.device}.{self.property_name}.{name}", value) for name, value in self.elements]


def load_world_settings(path: Path) -> dict[str, Any]:
    """Read the world's settings file.

    Parameters
    ----------
    path : `pathlib.Path`
        The settings file (TOML).

    Returns
    -------
    settings : `dict` [`str`, `~typing.Any`]
        The sections, or an empty dict when the file is missing or unreadable.
    """
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, tomllib.TOMLDecodeError:
        return {}


def _number(value: Any) -> float | None:
    """Read a settings value as a positive number.

    Parameters
    ----------
    value : `~typing.Any`
        A settings value (text or number).

    Returns
    -------
    number : `float` or `None`
        The value, or `None` when it is missing, not a number or not positive.
    """
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if number > 0 else None


def _camera_profile(settings: Mapping[str, Any], role_key: str) -> Mapping[str, Any]:
    """Find a camera's profile section.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings.
    role_key : `str`
        ``default_primary_camera`` or ``default_secondary_camera``.

    Returns
    -------
    profile : `~collections.abc.Mapping`
        The ``Observatory.Camera.<name>`` section, or an empty mapping.
    """
    name = str(settings.get("Observatory.Camera", {}).get(role_key, "")).strip()
    return settings.get(f"Observatory.Camera.{name}", {}) if name else {}


def _sensor_setting(device: str, profile: Mapping[str, Any]) -> DeviceSetting | None:
    """Build the resolution and pixel-size setting of one camera simulator.

    Parameters
    ----------
    device : `str`
        The simulator device.
    profile : `~collections.abc.Mapping`
        The camera's profile section.

    Returns
    -------
    setting : `DeviceSetting` or `None`
        `None` when the profile lacks the sensor size or the pixel size.
    """
    width = _number(profile.get("sensor_width_px"))
    height = _number(profile.get("sensor_height_px"))
    pixel = _number(profile.get("pixel_size_μm"))
    if width is None or height is None or pixel is None:
        return None
    return DeviceSetting(
        device,
        "SIMULATOR_SETTINGS",
        (("SIM_XRES", width), ("SIM_YRES", height), ("SIM_XSIZE", pixel), ("SIM_YSIZE", pixel)),
    )


def _capture_setting(profile: Mapping[str, Any]) -> tuple[float, float] | None:
    """Find the capture setting recorded in a camera profile.

    Parameters
    ----------
    profile : `~collections.abc.Mapping`
        The camera's profile section.

    Returns
    -------
    setting : `tuple` [`float`, `float`] or `None`
        ``(gain, offset)`` of the first bias level, else of the first noise
        measurement. `None` when neither is recorded or the gain is not a
        number (a DSLR's ISO is not set on the simulator).
    """
    for key in ("bias_levels", "sensor_noise"):
        entries = profile.get(key) or []
        if not entries:
            continue
        try:
            return float(entries[0]["gain_setting"]), float(entries[0]["offset"])
        except KeyError, TypeError, ValueError:
            continue
    return None


def _bias_level_adu(profile: Mapping[str, Any], gain: float, offset: float) -> float | None:
    """Find the real camera's bias level for one capture setting.

    Parameters
    ----------
    profile : `~collections.abc.Mapping`
        The camera's profile section.
    gain : `float`
        The gain setting.
    offset : `float`
        The camera offset setting.

    Returns
    -------
    level : `float` or `None`
        The ``bias_levels`` entry for this gain and offset, in ADU. `None`
        when none is recorded.
    """
    for entry in profile.get("bias_levels") or []:
        try:
            if not (
                math.isclose(float(entry["gain_setting"]), gain)
                and math.isclose(float(entry["offset"]), offset)
            ):
                continue
            recorded = entry["bias_level_adu"]
            level = _number(recorded["value"] if isinstance(recorded, Mapping) else recorded)
        except KeyError, TypeError, ValueError:
            continue
        if level is not None:
            return level
    return None


#: Slots in the INDI filter wheel simulator (fixed by the driver).
FILTER_WHEEL_SLOTS = 8
FILTER_WHEEL = "Filter Simulator"


def real_filter_names(settings: Mapping[str, Any]) -> list[str]:
    """Read the filter names from the settings, as the app does.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings.

    Returns
    -------
    names : `list` [`str`]
        The names in ``[Observatory.Filters] available`` (else ``owned``),
        in order, without blanks or repeats.
    """
    section = settings.get("Observatory.Filters", {})
    text = section.get("available", section.get("owned", ""))
    names: list[str] = []
    for raw in str(text).split(","):
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    return names


def _filter_settings(settings: Mapping[str, Any]) -> list[DeviceSetting]:
    """Build the filter wheel settings.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings.

    Returns
    -------
    properties : `list` [`DeviceSetting`]
        The camera's link to the wheel, then the slot names. Empty when the
        settings list no filters. A list longer than the wheel is cut.
    """
    names = real_filter_names(settings)[:FILTER_WHEEL_SLOTS]
    if not names:
        return []
    elements = tuple((f"FILTER_SLOT_NAME_{number}", name) for number, name in enumerate(names, start=1))
    return [
        DeviceSetting(MAIN_CAMERA, "ACTIVE_DEVICES", (("ACTIVE_FILTER", FILTER_WHEEL),)),
        DeviceSetting(FILTER_WHEEL, "FILTER_NAME", elements),
    ]


def camera_setpoint(profile: Mapping[str, Any]) -> float | None:
    """Find the cooler setpoint recorded in a camera profile.

    Parameters
    ----------
    profile : `~collections.abc.Mapping`
        The camera's profile section.

    Returns
    -------
    temperature : `float` or `None`
        ``temperature_c`` of the first bias level (else the first noise
        measurement) that has one. `None` when the profile records none.
    """
    for key in ("bias_levels", "sensor_noise"):
        for entry in profile.get(key) or []:
            try:
                return float(entry["temperature_c"])
            except KeyError, TypeError, ValueError:
                continue
    return None


def device_settings_from_world(settings: Mapping[str, Any]) -> list[DeviceSetting]:
    """List the INDI properties that make the simulators match the settings.

    The order matters: a resolution change makes the camera simulator
    redefine its properties, so the gain and offset come last.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings of the world.

    Returns
    -------
    properties : `list` [`DeviceSetting`]
        What to set. A setting with no source in the file is left out.
    """
    result: list[DeviceSetting] = _filter_settings(settings)
    primary = _camera_profile(settings, "default_primary_camera")
    secondary = _camera_profile(settings, "default_secondary_camera")
    for device, profile in ((MAIN_CAMERA, primary), (GUIDE_CAMERA, secondary)):
        sensor = _sensor_setting(device, profile)
        if sensor is not None:
            result.append(sensor)
    telescope = settings.get("Observatory.Telescope", {})
    focal_length = _number(telescope.get("focal_length_mm")) or FALLBACK_FOCAL_LENGTH_MM
    focal_ratio = _number(telescope.get("focal_ratio")) or FALLBACK_FOCAL_RATIO
    result.append(
        DeviceSetting(
            MAIN_CAMERA,
            "SCOPE_INFO",
            (("FOCAL_LENGTH", focal_length), ("APERTURE", focal_length / focal_ratio)),
        )
    )
    guide_scopes = settings.get("Observatory.GuideScope", {})
    guide_scope = settings.get(f"Observatory.GuideScope.{guide_scopes.get('active_guide_scope', '')}", {})
    guide_length = _number(guide_scope.get("focal_length_mm"))
    guide_aperture = _number(guide_scope.get("aperture_mm"))
    if guide_length is not None and guide_aperture is not None:
        result.append(
            DeviceSetting(
                GUIDE_CAMERA, "SCOPE_INFO", (("FOCAL_LENGTH", guide_length), ("APERTURE", guide_aperture))
            )
        )
    capture = _capture_setting(primary)
    if capture is not None:
        result.append(DeviceSetting(MAIN_CAMERA, "CCD_GAIN", (("GAIN", capture[0]),)))
        # INDI uses CCD_OFFSET as the empty-frame pixel value, so send the
        # real camera's bias level; the header keeps the camera offset.
        pedestal = _bias_level_adu(primary, capture[0], capture[1])
        if pedestal is None:
            LOGGER.warning(
                "No bias level is recorded for gain %s, offset %s; "
                "simulated frames will sit near %s ADU, not at the real bias level.",
                capture[0],
                capture[1],
                capture[1],
            )
            pedestal = capture[1]
        result.append(
            DeviceSetting(MAIN_CAMERA, "CCD_OFFSET", (("OFFSET", pedestal),), header_value=capture[1])
        )
    setpoint = camera_setpoint(primary)
    if setpoint is not None:
        result.append(
            DeviceSetting(MAIN_CAMERA, "CCD_TEMPERATURE", (("CCD_TEMPERATURE_VALUE", setpoint),), check=False)
        )
    return result


def matches(expected: float | str, found: str) -> bool:
    """Say whether a value read back from the server equals what was set.

    Parameters
    ----------
    expected : `float` or `str`
        The value that was set.
    found : `str`
        The text ``indi_getprop`` printed.

    Returns
    -------
    same : `bool`
        `True` when the numbers agree within `READ_BACK_TOLERANCE`.
    """
    if isinstance(expected, str):
        return found == expected
    try:
        value = float(found)
    except ValueError:
        return False
    return abs(value - expected) <= READ_BACK_TOLERANCE * max(1.0, abs(expected))
