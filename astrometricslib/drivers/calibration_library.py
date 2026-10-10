"""The library of dark, bias and flat frames, and how it picks them.

The library files every calibration frame under the camera settings that
decide whether the frame can calibrate a light frame, and picks the frames for
a batch of lights. Each frame's slot is a "setting key": its gain, its camera
offset and its binning, and for a dark also a temperature slot (see
`calibration_setting_key`). Frames with different keys never mix into one
master. When the lights have no frame under their own key, the library falls
back where that is safe, and says so in a plain sentence:

- A **gain or offset** mismatch falls back to the other settings. The frames
  are used, and the sentence is a soft flag (`CalibrationSelection.flags`).
- A **temperature** mismatch (no dark within the tolerance of the lights)
  falls back to the nearest-temperature dark, also with a soft flag.
- A **binning** mismatch never falls back. Frames binned differently have
  different pixels, so none is used, and the sentence is a blocking flag
  (`CalibrationSelection.blocking_flags`).

The stacking run copies the flags into its diagnostics, and the stacking
pipeline reports them in the input quality summary.
"""

import json
import logging
import math
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from astropy.io import fits
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from astrometricslib.drivers.camera_profile_store import camera_identity, record_name_for_camera
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.utilities.iso_text import iso_or_gain_values_match

logger = logging.getLogger(__name__)


# Standard-system transformation coefficients derived for ZWO
# ASI533MM Pro with Baader UBVR Bessel filter set (Antonov 2026,
# JAAVSO Vol. 54).
BAADER_BESSEL_ASI533_TRANSFORM_COEFFICIENTS = {
    "T_bv": {"value": 1.095, "error": 0.009},
    "T_b_bv": {"value": 0.080, "error": 0.012},
    "T_br": {"value": 1.144, "error": 0.013},
    "T_b_br": {"value": 0.050, "error": 0.006},
    "T_v_bv": {"value": -0.008, "error": 0.015},
    "T_vr": {"value": 1.255, "error": 0.033},
    "T_v_vr": {"value": -0.012, "error": 0.025},
    "T_r_vr": {"value": -0.221, "error": 0.030},
}


# How many degrees C a dark's capture temperature may differ from the light
# frames it calibrates. The library files darks in temperature slots this wide
# (see `temperature_slot_c`), and a dark master farther than this from the
# lights is flagged (see `dark_temperature_flag`). It is a first-pass
# estimate, not yet characterized against this camera's own measured
# dark-current curve: it is deliberately loose (CMOS dark current can already
# grow noticeably within a few degrees) so it flags real gaps rather than
# ordinary cooler-setpoint jitter. Confirmed against a real incident:
# Arcturus's 0.5s/gain-0 darks cluster at ~0C and ~-10C with nothing in
# between, while its light frames drifted from -2.9C to -10.7C, and Siril
# reported 57-64% negative pixels after dark subtraction against the
# resulting mixed-temperature master.
DEFAULT_DARK_TEMPERATURE_TOLERANCE_C = 3.0

# The key in `ImageProcessing.last_run_diagnostics` that holds the blocking
# calibration match flags, one sentence each. A flag here means a calibration
# kind was not applied at all because the library's frames could never
# calibrate the lights (for example, a binning mismatch). The stacking
# pipeline reads it (see `calibration_gates` in `pipelines/stacking/`
# `pre_processing/assess_input_quality.py`) and fails its
# ``calibration_metadata`` gate when the list is not empty.
CALIBRATION_MATCH_BLOCKING_FLAGS_KEY = "calibration_match_blocking_flags"

# The binning of a frame whose header names none: no binning at all.
UNBINNED = "1x1"

# The names of the parts of a library key after the gain, each written
# ``@<name>=<value>``, for example "0.0@offset=30@bin=2x2@temp=-9". A key
# with no such part means offset 0, 1x1 binning and no recorded temperature,
# which is every key the library held before these were tracked, so a saved
# library file needs no migration.
_OFFSET_KEY_NAME = "offset"
_BINNING_KEY_NAME = "bin"
_TEMPERATURE_KEY_NAME = "temp"

# What a selection calls the lights when the caller does not say which.
_DEFAULT_LIGHT_LABEL = "the light frames"


def _as_float(value: Any) -> float | None:
    """Read a header value as a number.

    Returns
    -------
    number : `float` or `None`
        The value as a float, or `None` if it is missing or not a number.
    """
    try:
        return None if value is None else float(value)
    except TypeError, ValueError:
        return None


def _binning_factor(value: Any) -> int | None:
    """Read one binning factor (the number of pixels merged along an axis).

    Returns
    -------
    factor : `int` or `None`
        The factor as a whole number of at least 1, or `None` if the value is
        missing, not a number, or below 1.
    """
    number = _as_float(value)
    if number is None or not math.isfinite(number) or number < 1:
        return None
    return int(number)


def format_binning(x_binning: Any = None, y_binning: Any = None) -> str:
    """Write a binning as text, such as ``"2x2"``.

    Binning merges neighbouring pixels on the sensor into one. A 2x2 frame has
    half as many pixels along each axis, and each pixel holds the signal (and
    the dark current and bias) of four sensor pixels. A frame binned one way
    can never calibrate a frame binned another way.

    Parameters
    ----------
    x_binning : `Any`, optional
        The binning along the width (the ``XBINNING`` card). Missing or not a
        number counts as 1.
    y_binning : `Any`, optional
        The binning along the height (the ``YBINNING`` card). Missing counts
        as the same as `x_binning`.

    Returns
    -------
    binning : `str`
        ``"<x>x<y>"``. ``"1x1"`` (`UNBINNED`) when there is no binning.
    """
    x_factor = _binning_factor(x_binning) or 1
    y_factor = _binning_factor(y_binning) or x_factor
    return f"{x_factor}x{y_factor}"


def normalize_binning(binning: Any) -> str:
    """Write a binning given as text or as a number in the standard form.

    Parameters
    ----------
    binning : `Any`
        Text such as ``"2x2"`` or ``"2X2"``, or a number such as ``2`` (the
        same factor along both axes). Anything else counts as unbinned.

    Returns
    -------
    binning : `str`
        The standard form, such as ``"2x2"`` (see `format_binning`).
    """
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(binning))
    if match:
        return format_binning(match.group(1), match.group(2))
    return format_binning(binning)


def header_binning(header: Any) -> str:
    """Read the binning of a frame from its header.

    Parameters
    ----------
    header : `Any`
        The image header (anything with ``get``).

    Returns
    -------
    binning : `str`
        The binning from the ``XBINNING`` and ``YBINNING`` cards, or
        ``"1x1"`` when the header has neither.
    """
    return format_binning(header.get("XBINNING"), header.get("YBINNING"))


def header_temperature_c(header: Any) -> float | None:
    """Read the sensor temperature of a frame from its header.

    Parameters
    ----------
    header : `Any`
        The image header (anything with ``get``).

    Returns
    -------
    temperature_c : `float` or `None`
        The ``CCD-TEMP`` card (the measured sensor temperature). When the
        header has none, the ``SET-TEMP`` card (the cooler's target), which
        is the best guess a cooled camera leaves. `None` if neither is a
        number.
    """
    for card in ("CCD-TEMP", "SET-TEMP"):
        value = _as_float(header.get(card))
        if value is not None and math.isfinite(value):
            return value
    return None


def temperature_slot_c(
    temperature_c: float | None, tolerance_c: float = DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
) -> float | None:
    """Round a sensor temperature to the middle of its temperature slot.

    Slots are `tolerance_c` wide, so darks whose temperatures round to the
    same slot differ by at most one tolerance and may be stacked into one
    master. The slot is ``round(temperature / tolerance) * tolerance``. With
    the default 3 C tolerance, -10.2 C and -9.0 C share the -9 C slot, and
    +5 C is in the +6 C slot.

    Parameters
    ----------
    temperature_c : `float` or `None`
        The frame's sensor temperature in degrees C.
    tolerance_c : `float`, optional
        The slot width in degrees C. Zero or less rounds to 0.1 C instead.

    Returns
    -------
    slot_c : `float` or `None`
        The slot's middle, or `None` if the temperature is unknown.
    """
    if temperature_c is None or not math.isfinite(temperature_c):
        return None
    if tolerance_c <= 0:
        return round(temperature_c, 1)
    return round(temperature_c / tolerance_c) * tolerance_c


def calibration_setting_key(
    gain: Any,
    offset: Any = None,
    binning: Any = None,
    temperature_c: float | None = None,
    temperature_tolerance_c: float = DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
) -> str:
    """Build the library key for a calibration frame's camera settings.

    A camera's offset is a baseline added to every pixel before it is
    digitized. A frame taken at one offset cannot calibrate frames taken at
    another, and at offset 0 much of the noise is clipped away, so the two
    settings must not share a slot in the library. The same holds for the
    gain, for the binning (a 2x2 frame has different pixels from a 1x1 one),
    and, for a dark, for the sensor temperature (dark current roughly doubles
    every few degrees).

    Parameters
    ----------
    gain : `Any`
        The gain or ISO, as written in the frame header.
    offset : `Any`, optional
        The camera offset. `None`, zero, or text that is not a number adds
        nothing to the key.
    binning : `Any`, optional
        The binning, such as ``"2x2"`` (see `normalize_binning`). `None` or
        1x1 adds nothing to the key.
    temperature_c : `float`, optional
        The sensor temperature of a dark frame. It is rounded to a slot
        `temperature_tolerance_c` wide (see `temperature_slot_c`). Leave it
        out for bias and flat frames and for darks with no temperature.
    temperature_tolerance_c : `float`, optional
        The slot width in degrees C.

    Returns
    -------
    key : `str`
        The gain as text, followed by ``@offset=<offset>`` when the offset is
        a nonzero number, ``@bin=<binning>`` when the binning is not 1x1, and
        ``@temp=<slot>`` when a temperature is given.
    """
    key = str(gain)
    offset_value = _as_float(offset)
    # A missing or zero offset adds nothing to the key.
    if offset_value:
        key += f"@{_OFFSET_KEY_NAME}={offset_value:g}"
    if binning is not None and normalize_binning(binning) != UNBINNED:
        key += f"@{_BINNING_KEY_NAME}={normalize_binning(binning)}"
    slot = temperature_slot_c(temperature_c, temperature_tolerance_c)
    if slot is not None:
        key += f"@{_TEMPERATURE_KEY_NAME}={slot:g}"
    return key


@dataclass(frozen=True)
class CalibrationSetting:
    """The camera settings that a library key stands for.

    Attributes
    ----------
    gain : `str`
        The gain (or ISO) setting, as text.
    offset : `float`
        The camera offset (0.0 when the key has none).
    binning : `str`
        The binning, such as ``"2x2"`` (``"1x1"`` when the key has none).
    temperature_c : `float` or `None`
        The middle of the key's temperature slot, or `None` when the key has
        no temperature (every bias and flat key, and darks filed before the
        temperature was tracked or whose header had none).
    """

    gain: str
    offset: float
    binning: str
    temperature_c: float | None


def parse_calibration_setting_key(key: str) -> CalibrationSetting:
    """Read a library key back into the settings it stands for.

    Parameters
    ----------
    key : `str`
        A key made by `calibration_setting_key`, or an older gain-only key.

    Returns
    -------
    setting : `CalibrationSetting`
        The gain, offset, binning and temperature slot of the key.
    """
    gain, *parts = str(key).split("@")
    offset = 0.0
    binning = UNBINNED
    temperature_c = None
    for part in parts:
        name, _, value = part.partition("=")
        if name == _OFFSET_KEY_NAME:
            offset = _as_float(value) or 0.0
        elif name == _BINNING_KEY_NAME:
            binning = normalize_binning(value)
        elif name == _TEMPERATURE_KEY_NAME:
            temperature_c = _as_float(value)
    return CalibrationSetting(gain, offset, binning, temperature_c)


def split_calibration_setting_key(key: str) -> tuple[str, float]:
    """Split a library key back into its gain and offset.

    Parameters
    ----------
    key : `str`
        A key made by `calibration_setting_key`, or an older gain-only key.

    Returns
    -------
    gain, offset : `tuple` [`str`, `float`]
        The gain text, and the offset (0.0 when the key has none).
    """
    setting = parse_calibration_setting_key(key)
    return setting.gain, setting.offset


def _is_same_setting(key: str, gain: Any, offset: Any) -> bool:
    """Tell whether a library key is the given gain and offset.

    Gains are compared as numbers when both are numbers ("0" equals "0.0"),
    and as text otherwise (an ISO such as "800"). Binning and temperature are
    not compared here.

    Returns
    -------
    is_same : `bool`
        `True` if the key's gain and offset both match.
    """
    key_gain, key_offset = split_calibration_setting_key(key)
    key_gain_number, wanted_gain_number = _as_float(key_gain), _as_float(gain)
    if key_gain_number is not None and wanted_gain_number is not None:
        gain_matches = key_gain_number == wanted_gain_number
    else:
        gain_matches = key_gain.strip() == str(gain).strip()
    wanted_offset = _as_float(offset)
    return gain_matches and key_offset == (0.0 if wanted_offset is None else wanted_offset)


@dataclass(frozen=True)
class FlatGroup:
    """The flat frames filed under one set of settings.

    Attributes
    ----------
    telescope : `str`
        The telescope the flats were taken through.
    camera : `str`
        The camera name the flats are filed under.
    filter : `str`
        The filter name the flats are filed under.
    gain : `str`
        The gain (or ISO) setting, as text.
    offset : `float`
        The camera offset (0.0 when the flats record none).
    paths : `list` [`str`]
        The flat frame file paths.
    binning : `str`
        The binning the flats were taken at, such as ``"2x2"``.
    """

    telescope: str
    camera: str
    filter: str
    gain: str
    offset: float
    paths: list[str]
    binning: str = UNBINNED


@dataclass(frozen=True)
class CalibrationSelection:
    """The calibration frames chosen for a batch of lights, and why.

    Attributes
    ----------
    frames : `list` [`str`]
        The chosen frame file paths. Empty when no frame fits, or when a
        blocking flag says none may be used.
    flags : `list` [`str`]
        One plain sentence for each mismatch that the library worked around:
        frames of another gain or offset, or a dark from another temperature,
        were used because nothing closer exists. The frames in `frames` are
        still applied.
    blocking_flags : `list` [`str`]
        One plain sentence for each mismatch that stopped the frames from
        being used at all (a binning mismatch). `frames` is empty then.
    """

    frames: list[str]
    flags: list[str] = field(default_factory=list)
    blocking_flags: list[str] = field(default_factory=list)


def _without_duplicates(paths: list[str]) -> list[str]:
    """Drop repeated paths from a list, keeping the first of each in order.

    The same file can be listed under more than one gain, offset or filter
    alias, and a frame that is listed twice is counted and stacked twice.

    Parameters
    ----------
    paths : `list` [`str`]
        Frame paths, possibly with repeats.

    Returns
    -------
    unique_paths : `list` [`str`]
        The paths with each one appearing once.
    """
    return list(dict.fromkeys(paths))


def _existing(paths: Iterable[str], validate_paths: bool) -> list[str]:
    """Keep only the paths whose files exist, when asked to.

    Returns
    -------
    kept : `list` [`str`]
        The paths without repeats, and without missing files when
        `validate_paths` is `True`.
    """
    unique = _without_duplicates(list(paths))
    return [path for path in unique if os.path.exists(path)] if validate_paths else unique


def _describe_settings(keys: Iterable[str]) -> str:
    """Name the gain and offset pairs that some library keys stand for.

    Returns
    -------
    text : `str`
        For example ``"gain 100, offset 30"``; several pairs are joined with
        " or ".
    """
    pairs = sorted({(setting.gain, setting.offset) for setting in map(parse_calibration_setting_key, keys)})
    return " or ".join(f"gain {gain}, offset {offset:g}" for gain, offset in pairs)


def _choose_slots(
    slots: dict[str, list[str]],
    gain: Any,
    offset: Any,
    binning: Any,
    kind: str,
    light_label: str,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Pick the library slots that can calibrate the lights.

    The binning must match exactly. If frames exist but none has the lights'
    binning, nothing is chosen and a blocking flag says why. Among the slots
    with the right binning, those at the lights' gain and offset are chosen
    when there are any. Otherwise every slot is chosen, with a soft flag,
    because a calibration frame at other settings is judged better than none.

    Parameters
    ----------
    slots : `dict` [`str`, `list` [`str`]]
        The slot keys that hold frames, with their file paths.
    gain : `Any`
        The lights' gain, or `None` to take every gain and offset without a
        flag (a caller that does not say what the lights used).
    offset : `Any`
        The lights' camera offset.
    binning : `Any`
        The lights' binning, or `None` to take every binning.
    kind : `str`
        ``"dark"``, ``"bias"`` or ``"flat"``, for the sentences.
    light_label : `str`
        How the sentences name the lights.

    Returns
    -------
    chosen, flags, blocking_flags : `tuple`
        The chosen slots with their paths, the soft flag sentences and the
        blocking flag sentences.
    """
    flags: list[str] = []
    blocking_flags: list[str] = []
    slots = {key: paths for key, paths in slots.items() if paths}
    if binning is not None:
        wanted_binning = normalize_binning(binning)
        same_binning = {
            key: paths
            for key, paths in slots.items()
            if parse_calibration_setting_key(key).binning == wanted_binning
        }
        if slots and not same_binning:
            others = sorted({parse_calibration_setting_key(key).binning for key in slots})
            blocking_flags.append(
                f"No {kind} was applied to {light_label}: they were taken at binning "
                f"{wanted_binning}, but the {kind} frames in the library were taken at binning "
                f"{' or '.join(others)}, and frames binned differently cannot calibrate each other."
            )
            return {}, flags, blocking_flags
        slots = same_binning
    if gain is None or not slots:
        return slots, flags, blocking_flags
    exact = {key: paths for key, paths in slots.items() if _is_same_setting(key, gain, offset)}
    if exact:
        return exact, flags, blocking_flags
    wanted_offset = _as_float(offset)
    offset_text = f"{0.0 if wanted_offset is None else wanted_offset:g}"
    flags.append(
        f"No {kind} frames matched gain {gain} and offset {offset_text}, "
        f"so {kind} frames taken at {_describe_settings(slots)} were applied to {light_label}."
    )
    return slots, flags, blocking_flags


def _dark_paths_at_exposure(exposure_slots: dict[str, Any], exposure: Any) -> list[str]:
    """Collect the dark frames whose exposure fits the lights'.

    Parameters
    ----------
    exposure_slots : `dict` [`str`, `list` [`str`]]
        One gain slot's darks, by exposure in seconds (as text).
    exposure : `Any`
        The lights' exposure in seconds; darks within 0.1 s of it are used.
        When `None` (or not a number), every exposure is used.

    Returns
    -------
    paths : `list` [`str`]
        The matching dark frame paths.
    """
    try:
        target_exposure = float(exposure) if exposure is not None else None
    except ValueError, TypeError:
        target_exposure = None
    collected: list[str] = []
    for exposure_key, file_list in exposure_slots.items():
        try:
            # Fuzzy exposure match (allow 0.1s jitter)
            if target_exposure is None or abs(float(exposure_key) - target_exposure) < 0.1:
                collected.extend(file_list)
        except ValueError, TypeError:
            # Fallback to exact string match
            if str(exposure) == exposure_key:
                collected.extend(file_list)
    return collected


def _read_dark_temperature(path: str) -> float | None:
    """Read the sensor temperature a dark frame was taken at.

    Returns
    -------
    temperature_c : `float` or `None`
        The temperature from the frame's header (see `header_temperature_c`),
        or `None` if the file cannot be read or records none.
    """
    try:
        with fits.open(path, memmap=False) as hdu_list:
            return header_temperature_c(hdu_list[0].header)
    except FITS_READ_ERRORS:
        return None


def dark_temperature_flag(
    light_temperature_c: float,
    dark_paths: list[str],
    tolerance_c: float = DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    light_label: str = _DEFAULT_LIGHT_LABEL,
) -> str | None:
    """Say whether a dark master is too far from the lights' temperature.

    Every dark's own ``CCD-TEMP`` is read, not only the first. The master is
    the combination of all of them, so its temperature is their mean. The
    sentence also gives the coldest and warmest dark, because a master built
    from darks that span a wide range hides how each frame behaves.

    Parameters
    ----------
    light_temperature_c : `float`
        The lights' sensor temperature in degrees C (for example the mean over
        the batch).
    dark_paths : `list` [`str`]
        The dark frames that make the master.
    tolerance_c : `float`, optional
        How many degrees C the master may differ from the lights.
    light_label : `str`, optional
        How the sentence names the lights.

    Returns
    -------
    flag : `str` or `None`
        One sentence naming the lights' temperature, the master's temperature
        and the tolerance when the master is farther away than the tolerance.
        `None` when it is within the tolerance, or when no dark records a
        temperature (there is nothing to judge).
    """
    temperatures = [t for t in map(_read_dark_temperature, dark_paths) if t is not None]
    if not temperatures:
        return None
    master_temperature_c = sum(temperatures) / len(temperatures)
    if is_dark_calibration_temperature_compatible(light_temperature_c, master_temperature_c, tolerance_c):
        return None
    difference_c = abs(light_temperature_c - master_temperature_c)
    return (
        f"No dark frame was within {tolerance_c:g} C of {light_label}, which were taken at "
        f"{light_temperature_c:.1f} C, so the nearest dark master, taken at "
        f"{master_temperature_c:.1f} C ({len(temperatures)} dark frame(s) from "
        f"{min(temperatures):.1f} to {max(temperatures):.1f} C), was applied, "
        f"{difference_c:.1f} C away."
    )


def _drop_path(node: Any, path: str) -> None:
    """Remove a file path from every list below a library node.

    Lists and dictionaries that the removal leaves empty are dropped, so the
    library does not keep a slot with no frames.

    Parameters
    ----------
    node : `Any`
        A list of paths, or a dictionary of such lists nested to any depth.
    path : `str`
        The file path to remove.
    """
    if isinstance(node, list):
        node[:] = [item for item in node if item != path]
    elif isinstance(node, dict):
        for key in list(node):
            _drop_path(node[key], path)
            if not node[key]:
                del node[key]


def _move_out_of_other_slots(slots: dict[str, Any], keep_key: str, path: str) -> None:
    """Remove a frame from every slot except the one it is being filed under.

    A frame is filed under the key made from its header. A library saved by an
    older version filed the same frame under a shorter key (without binning or
    temperature). Without this, a rescan would list the frame twice, and the
    old slot would pool it back in with the frames of every other temperature.

    Parameters
    ----------
    slots : `dict` [`str`, `Any`]
        The setting keys of one camera (or filter), with their frame lists.
    keep_key : `str`
        The key the frame is being filed under now. Its slot is left alone.
    path : `str`
        The frame's file path.
    """
    for key in list(slots):
        if key != keep_key:
            _drop_path(slots[key], path)
            if not slots[key]:
                del slots[key]


class CalibrationLibrary(BaseModel):
    """Store dark, flat, and bias calibration frames."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    dark_frames: dict[str, Any] = Field(default_factory=dict)
    flat_frames: dict[str, Any] = Field(default_factory=dict)
    bias_frames: dict[str, Any] = Field(default_factory=dict)

    # Internal config state, excluded from serialization
    app_config: Any = Field(default=None, exclude=True)
    _lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)

    def __init__(self, app_config=None, **data) -> None:  # ruff: ignore[missing-type-function-argument, missing-type-kwargs]
        """Initialize calibration library with optional shared config."""
        super().__init__(**data)
        if app_config is None:
            from astrometricslib.foundation.config import get_configuration

            self.app_config = get_configuration()
        else:
            self.app_config = app_config
        # Data is loaded explicitly by the container now, but we keep
        # safety check
        if self.app_config:
            self.load_library()

    def load_library(self) -> None:
        """Load the calibration library from the JSON file."""
        if not self.app_config:
            return

        try:
            with self._lock:
                file_path = self.app_config.get_library_file_path("calibration_frames.json")
                if os.path.exists(file_path):
                    with open(file_path, encoding="utf8") as file_obj:
                        calibration_info = json.load(file_obj)
                        self.deserialize(calibration_info)
        except OSError, ValueError, KeyError, TypeError:
            logger.exception("Could not load calibration_frames.json")

    def save_library(self) -> None:
        """Save calibration library."""
        if not self.app_config:
            return

        try:
            with self._lock:
                file_path = self.app_config.get_library_file_path("calibration_frames.json")
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding="utf8") as file_obj:
                    json.dump(self.serialize(), file_obj, indent=4)
        except OSError, ValueError, TypeError:
            logger.exception("Could not save calibration_frames.json")

    def serialize(self) -> dict[str, Any]:
        """Serialize this calibration library to a plain dict.

        Returns
        -------
        data : `dict`
            Plain dict representation of this calibration library.
        """
        return self.model_dump()

    def deserialize(self, object_info: dict[str, Any]) -> None:
        """Populate this calibration library from a serialized dict.

        Parameters
        ----------
        object_info : `dict`
            A dict as produced by `serialize`, mapping field names
            (``dark_frames``, ``flat_frames``, ``bias_frames``) to
            their values. Non-dict input and empty/falsy values are
            silently ignored.
        """
        if not isinstance(object_info, dict):
            return
        for property_id, value in object_info.items():
            if hasattr(self, property_id) and value:
                setattr(self, property_id, value)

    def get_stats(self) -> dict[str, Any]:
        """Get aggregated statistics of calibration frames in the library.

        Covers dark, bias, and flat frames.



        Returns
        -------
        stats : `dict` [`str`, `Any`]
            Dict with ``"darks"``, ``"biases"``, and ``"flats"`` keys,
            each a list of per-group count summaries.
        """
        darks = []
        biases = []
        flats = []

        with self._lock:
            # 1. Dark frames stats
            for camera, iso_dict in self.dark_frames.items():
                if not isinstance(iso_dict, dict):
                    continue
                for iso, exp_dict in iso_dict.items():
                    if not isinstance(exp_dict, dict):
                        continue
                    for exp, file_list in exp_dict.items():
                        if not isinstance(file_list, list):
                            continue
                        try:
                            exposure = float(exp)
                        except ValueError, TypeError:
                            exposure = None
                        setting = parse_calibration_setting_key(iso)
                        darks.append({
                            "camera": camera,
                            "iso": setting.gain,
                            "offset": setting.offset,
                            "binning": setting.binning,
                            "temperature_c": setting.temperature_c,
                            "exposure": exposure,
                            "count": len(file_list),
                        })

            # 2. Bias frames stats
            for camera, iso_dict in self.bias_frames.items():
                if not isinstance(iso_dict, dict):
                    continue
                for iso, file_list in iso_dict.items():
                    if not isinstance(file_list, list):
                        continue
                    setting = parse_calibration_setting_key(iso)
                    biases.append({
                        "camera": camera,
                        "iso": setting.gain,
                        "offset": setting.offset,
                        "binning": setting.binning,
                        "count": len(file_list),
                    })

            # 3. Flat frames stats
            for _telescope, camera_dict in self.flat_frames.items():
                if not isinstance(camera_dict, dict):
                    continue
                for camera, filter_dict in camera_dict.items():
                    if not isinstance(filter_dict, dict):
                        continue
                    for filt, iso_dict in filter_dict.items():
                        if not isinstance(iso_dict, dict):
                            continue
                        for iso, file_list in iso_dict.items():
                            if not isinstance(file_list, list):
                                continue
                            setting = parse_calibration_setting_key(iso)
                            flats.append({
                                "camera": camera,
                                "filter": filt,
                                "iso": setting.gain,
                                "offset": setting.offset,
                                "binning": setting.binning,
                                "count": len(file_list),
                            })

        return {"darks": darks, "biases": biases, "flats": flats}

    def _get_iso_gain(self, header) -> str:  # ruff: ignore[missing-type-function-argument]
        """Extract ISO or GAIN from header.

        Returns
        -------
        iso_or_gain : `str`
            The resolved ISO/GAIN value as a string.
        """
        iso = header.get("ISOSPEED")
        if iso is None:
            iso = header.get("GAIN")
        if iso is None:
            iso = "800"
        return str(iso)

    def _get_offset(self, header) -> Any:  # ruff: ignore[missing-type-function-argument]
        """Extract the camera offset from a header.

        Returns
        -------
        offset : `Any`
            The ``OFFSET`` value, else ``BLKLEVEL``, else `None` when the
            header records neither.
        """
        offset = header.get("OFFSET")
        return header.get("BLKLEVEL") if offset is None else offset

    def _get_camera_name(self, header: Any) -> str:
        """Give the camera name to file a calibration frame under.

        Parameters
        ----------
        header : `Any`
            The image header.

        Returns
        -------
        camera_name : `str`
            The camera's ``record_name`` from its profile, which is the
            spelling frame records use, or the header's own text when the
            camera has none.
        """
        return record_name_for_camera(header.get("INSTRUME", header.get("CAMERA", "Unknown")))

    def add_dark_frame(self, image_file: str) -> None:
        """Add a dark frame to the library.

        The dark is filed under its camera, then its setting key (gain,
        offset, binning and temperature slot; see `calibration_setting_key`),
        then its exposure. A dark that was already filed under another key,
        such as an older key with no binning or temperature, is moved to the
        new key, so a rescan of the library re-files it.

        Parameters
        ----------
        image_file : `str`
            Path to the dark frame. Files that are not FITS are ignored.
        """
        if not image_file.lower().endswith((".fits", ".fit")):
            return

        try:
            with fits.open(image_file, memmap=False) as hdu_list:
                header_info = hdu_list[0].header
                camera = self._get_camera_name(header_info)
                iso_speed = calibration_setting_key(
                    self._get_iso_gain(header_info),
                    self._get_offset(header_info),
                    binning=header_binning(header_info),
                    temperature_c=header_temperature_c(header_info),
                )
                exposure_time = str(header_info.get("EXPTIME", "30.0"))

                with self._lock:
                    camera_slots = self.dark_frames.setdefault(camera, {})
                    _move_out_of_other_slots(camera_slots, iso_speed, image_file)
                    frames = camera_slots.setdefault(iso_speed, {}).setdefault(exposure_time, [])
                    if image_file not in frames:
                        frames.append(image_file)
        except FITS_READ_ERRORS:
            logger.exception("Error adding dark frame %s", image_file)

    def add_bias_frame(self, image_file: str) -> None:
        """Add a bias frame to the library.

        The bias is filed under its camera, then its setting key (gain,
        offset and binning). A bias frame has no temperature slot, because
        the library does not treat the bias level as temperature dependent.

        Parameters
        ----------
        image_file : `str`
            Path to the bias frame. Files that are not FITS are ignored.
        """
        if not image_file.lower().endswith((".fits", ".fit")):
            return

        try:
            with fits.open(image_file, memmap=False) as hdu_list:
                header_info = hdu_list[0].header
                camera = self._get_camera_name(header_info)
                iso_speed = calibration_setting_key(
                    self._get_iso_gain(header_info),
                    self._get_offset(header_info),
                    binning=header_binning(header_info),
                )

                with self._lock:
                    camera_slots = self.bias_frames.setdefault(camera, {})
                    _move_out_of_other_slots(camera_slots, iso_speed, image_file)
                    frames = camera_slots.setdefault(iso_speed, [])
                    if image_file not in frames:
                        frames.append(image_file)
        except FITS_READ_ERRORS:
            logger.exception("Error adding bias frame %s", image_file)

    def add_flat_frame(self, image_file: str, telescope: str = "Unknown") -> None:
        """Add a flat frame to the library.

        The flat is filed under its telescope, camera and filter, then its
        setting key (gain, offset and binning).

        Parameters
        ----------
        image_file : `str`
            Path to the flat frame. Files that are not FITS are ignored.
        telescope : `str`, optional
            The telescope the flat was taken through.
        """
        if not image_file.lower().endswith((".fits", ".fit")):
            return

        from astrometricslib.drivers.filter_detection import get_filter_type

        try:
            with fits.open(image_file, memmap=False) as hdu_list:
                header_info = hdu_list[0].header
                camera = self._get_camera_name(header_info)

                filter_type = get_filter_type(header_info)
                filter_val = filter_type.value if hasattr(filter_type, "value") else str(filter_type)
                iso_speed = calibration_setting_key(
                    self._get_iso_gain(header_info),
                    self._get_offset(header_info),
                    binning=header_binning(header_info),
                )

                with self._lock:
                    filter_slots = (
                        self.flat_frames
                        .setdefault(telescope, {})
                        .setdefault(camera, {})
                        .setdefault(filter_val, {})
                    )
                    _move_out_of_other_slots(filter_slots, iso_speed, image_file)
                    frames = filter_slots.setdefault(iso_speed, [])
                    if image_file not in frames:
                        frames.append(image_file)
        except FITS_READ_ERRORS:
            logger.exception("Error adding flat frame %s", image_file)

    def check_for_calibration_frames(
        self,
        iso: Any | None = None,
        exposure: Any | None = None,
        camera: str | None = None,
        telescope: str | None = None,
        filter_type: Any | None = None,
    ) -> bool:
        """Check whether dark, bias, and flat frames all exist.

        Parameters
        ----------
        iso : `Any`, optional
            Unused; accepted for call-site compatibility, by default
            `None`.
        exposure : `Any`, optional
            Exposure time to look up dark frames for, by default
            `None`.
        camera : `str`, optional
            Camera name to look up frames for. If `None` (default),
            falls back to the library's only camera if it has just one.
        telescope : `str`, optional
            Telescope name to look up flat frames for. If `None`
            (default), falls back to the library's only telescope if
            it has just one.
        filter_type : `Any`, optional
            Filter to look up flat frames for, by default `None`.

        Returns
        -------
        has_all_frames : `bool`
            `True` if at least one dark, bias, and flat frame each
            exist for the given parameters; `False` otherwise
            (including if a stored exposure or setting cannot be read).
        """
        try:
            darks = self.get_dark_frames(camera=camera, exposure=exposure)
            biases = self.get_bias_frames(camera=camera)
            flats = self.get_flat_frames(telescope=telescope, camera=camera, filter_type=filter_type)
            return bool(darks and biases and flats)
        except ValueError, TypeError, KeyError:
            return False

    def _find_camera_key(self, frame_dict: dict[str, Any], camera: str) -> str | None:
        """Find the key a camera is filed under, allowing fuzzy matching.

        Returns
        -------
        camera_key : `str` or `None`
            The matching key, or `None` if no key matches.
        """
        if not camera or camera == "Unknown":
            # Fallback to first camera if only one exists
            if len(frame_dict) == 1:
                return next(iter(frame_dict))
            return None

        if camera in frame_dict:
            return camera

        # The same camera under another spelling: "Nikon D5300" is
        # "Nikon DSLR DSC D5300", found through the camera's profile.
        wanted_identity = camera_identity(camera)
        for key in frame_dict:
            if camera_identity(key) == wanted_identity:
                return key

        # Partial names: a name that is part of another, or contains it.
        for key in frame_dict:
            if camera.upper() in key.upper() or key.upper() in camera.upper():
                return key

        return None

    def _get_camera_dict(self, frame_dict: dict[str, Any], camera: str) -> dict[str, Any]:
        """Find camera data in `frame_dict`, allowing fuzzy matching.

        Returns
        -------
        camera_data : `dict`
            The matching camera's frame data, or an empty dict if no
            match is found.
        """
        camera_key = self._find_camera_key(frame_dict, camera)
        return {} if camera_key is None else frame_dict[camera_key]

    def select_dark_frames(
        self,
        camera: str | None = None,
        exposure: Any | None = None,
        validate_paths: bool = True,
        iso: Any | None = None,
        offset: Any | None = None,
        binning: Any | None = None,
        temperature_c: float | None = None,
        temperature_tolerance_c: float = DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
        light_label: str = _DEFAULT_LIGHT_LABEL,
    ) -> CalibrationSelection:
        """Choose the dark frames for a batch of lights, and how well they fit.

        The darks are narrowed in this order:

        1. **Exposure.** Darks within 0.1 s of the lights' exposure are kept.
        2. **Binning.** The lights' binning must match exactly. If darks fit
           the exposure but none has the lights' binning, no dark is chosen
           and a blocking flag says why.
        3. **Gain and offset.** Darks at the lights' gain and offset are
           kept. If there are none, darks at every other gain and offset are
           used, with a soft flag naming both.
        4. **Temperature.** Of the temperature slots that remain, the one
           nearest the lights' temperature is used (ties go to the colder
           slot, because a warmer dark would over-subtract). The header of
           every dark in it is read, and if the mean is farther from the
           lights than `temperature_tolerance_c`, a soft flag names both
           temperatures and the tolerance. Darks with no recorded
           temperature are used only when no dark has one.

        Parameters
        ----------
        camera : `str`, optional
            The camera name.
        exposure : `Any`, optional
            The light frames' exposure in seconds. When `None`, every
            exposure is used.
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`.
        iso : `Any`, optional
            The light frames' gain or ISO. When `None`, every gain and offset
            is used with no flag.
        offset : `Any`, optional
            The light frames' camera offset.
        binning : `Any`, optional
            The light frames' binning, such as ``"2x2"``. When `None`, every
            binning is used with no flag.
        temperature_c : `float`, optional
            The light frames' sensor temperature in degrees C (for example
            their mean). When `None`, it is unknown: if the darks span
            several temperature slots, the slot with the most darks is used,
            with a soft flag.
        temperature_tolerance_c : `float`, optional
            How many degrees C the dark master may differ from the lights
            before it is flagged.
        light_label : `str`, optional
            How the flag sentences name the lights.

        Returns
        -------
        selection : `CalibrationSelection`
            The dark file paths, with the soft and blocking flag sentences.
        """
        return self._select_dark_frames(
            camera,
            exposure,
            validate_paths,
            iso,
            offset,
            binning,
            temperature_c,
            temperature_tolerance_c,
            light_label,
            judge_temperature=True,
        )

    def _select_dark_frames(
        self,
        camera: str | None,
        exposure: Any | None,
        validate_paths: bool,
        iso: Any | None,
        offset: Any | None,
        binning: Any | None,
        temperature_c: float | None,
        temperature_tolerance_c: float,
        light_label: str,
        judge_temperature: bool,
    ) -> CalibrationSelection:
        """Do the work of `select_dark_frames`, or skip the temperature.

        Parameters
        ----------
        camera, exposure, validate_paths, iso, offset, binning
            As in `select_dark_frames`.
        temperature_c, temperature_tolerance_c, light_label
            As in `select_dark_frames`.
        judge_temperature : `bool`
            When `False`, every temperature slot is used and no temperature
            flag is made (the way `get_dark_frames` works when it is given no
            temperature).

        Returns
        -------
        selection : `CalibrationSelection`
            The dark file paths, with the soft and blocking flag sentences.
        """
        with self._lock:
            camera_data = self._get_camera_dict(self.dark_frames, camera)
            slots = {
                setting_key: _existing(_dark_paths_at_exposure(exposure_slots, exposure), validate_paths)
                for setting_key, exposure_slots in camera_data.items()
                if isinstance(exposure_slots, dict)
            }
        chosen, flags, blocking_flags = _choose_slots(slots, iso, offset, binning, "dark", light_label)

        by_temperature: dict[float | None, list[str]] = {}
        for setting_key, paths in chosen.items():
            slot_c = parse_calibration_setting_key(setting_key).temperature_c
            by_temperature.setdefault(slot_c, []).extend(paths)
        recorded = {slot_c: paths for slot_c, paths in by_temperature.items() if slot_c is not None}

        if not judge_temperature or not recorded:
            frames = [path for paths in chosen.values() for path in paths]
        elif temperature_c is None:
            # Largest set first; the colder slot wins a tie.
            slot_c = max(recorded, key=lambda slot: (len(recorded[slot]), -slot))
            frames = recorded[slot_c]
            if len(recorded) > 1:
                flags.append(
                    f"The temperature of {light_label} is not recorded and the library holds darks "
                    f"at {len(recorded)} temperatures ({', '.join(f'{s:g} C' for s in sorted(recorded))}), "
                    f"so the {len(frames)} dark frame(s) near {slot_c:g} C, the largest set, were "
                    f"applied without a temperature check."
                )
        else:
            slot_c = min(recorded, key=lambda slot: (abs(slot - temperature_c), slot))
            frames = recorded[slot_c]
            temperature_flag = dark_temperature_flag(
                temperature_c, _without_duplicates(frames), temperature_tolerance_c, light_label
            )
            if temperature_flag is not None:
                flags.append(temperature_flag)

        for flag in flags:
            logger.warning(flag)
        for flag in blocking_flags:
            logger.warning(flag)
        return CalibrationSelection(_without_duplicates(frames), flags, blocking_flags)

    def get_dark_frames(
        self,
        camera: str | None = None,
        exposure: Any | None = None,
        validate_paths: bool = True,
        iso: Any | None = None,
        offset: Any | None = None,
        binning: Any | None = None,
        temperature_c: float | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Retrieve dark frames for a camera, exposure and settings.

        This is `select_dark_frames` without the flags. Darks taken at the
        given gain and camera offset are used when there are any for the
        exposure. When there are none, darks from every other gain and offset
        are used instead, with a warning, since a mismatched dark is judged
        better than none. A binning that no dark has gives an empty list.
        When `iso` is not given, every gain and offset is used. When
        `binning` is not given, every binning is used. When `temperature_c`
        is not given, darks at every temperature are returned together, so
        pass it unless the aim is to list everything.

        Parameters
        ----------
        camera : `str`, optional
            The camera name.
        exposure : `Any`, optional
            The light frames' exposure in seconds; darks within 0.1 s of it
            are used. When `None`, every exposure is used.
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`.
        iso : `Any`, optional
            The light frames' gain or ISO.
        offset : `Any`, optional
            The light frames' camera offset.
        binning : `Any`, optional
            The light frames' binning, such as ``"2x2"``.
        temperature_c : `float`, optional
            The light frames' sensor temperature in degrees C.
        **kwargs : `Any`
            Ignored; accepted so older call sites keep working.

        Returns
        -------
        frames : `list` [`str`]
            Matching dark frame file paths, filtered to existing
            files if `validate_paths` is `True`.
        """
        return self._select_dark_frames(
            camera,
            exposure,
            validate_paths,
            iso,
            offset,
            binning,
            temperature_c,
            DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
            _DEFAULT_LIGHT_LABEL,
            judge_temperature=temperature_c is not None,
        ).frames

    def select_bias_frames(
        self,
        camera: str | None = None,
        validate_paths: bool = True,
        iso: Any | None = None,
        offset: Any | None = None,
        binning: Any | None = None,
        light_label: str = _DEFAULT_LIGHT_LABEL,
    ) -> CalibrationSelection:
        """Choose the bias frames for a batch of lights, and how well they fit.

        The binning must match exactly; if bias frames exist but none has the
        lights' binning, none is chosen and a blocking flag says why. Bias
        frames at the lights' gain and offset are used when there are any;
        otherwise every gain and offset is used, with a soft flag. Bias
        frames have no temperature slot.

        Parameters
        ----------
        camera : `str`, optional
            The camera name.
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`.
        iso : `Any`, optional
            The light frames' gain or ISO. When `None`, every gain and offset
            is used with no flag.
        offset : `Any`, optional
            The light frames' camera offset.
        binning : `Any`, optional
            The light frames' binning, such as ``"2x2"``. When `None`, every
            binning is used with no flag.
        light_label : `str`, optional
            How the flag sentences name the lights.

        Returns
        -------
        selection : `CalibrationSelection`
            The bias file paths, with the soft and blocking flag sentences.
        """
        with self._lock:
            camera_data = self._get_camera_dict(self.bias_frames, camera)
            slots = {
                setting_key: _existing(paths, validate_paths)
                for setting_key, paths in camera_data.items()
                if isinstance(paths, list)
            }
        chosen, flags, blocking_flags = _choose_slots(slots, iso, offset, binning, "bias", light_label)
        for flag in [*flags, *blocking_flags]:
            logger.warning(flag)
        frames = _without_duplicates([path for paths in chosen.values() for path in paths])
        return CalibrationSelection(frames, flags, blocking_flags)

    def get_bias_frames(
        self,
        camera: str | None = None,
        validate_paths: bool = True,
        iso: Any | None = None,
        offset: Any | None = None,
        binning: Any | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Retrieve bias frames for a camera, gain, offset and binning.

        This is `select_bias_frames` without the flags. Bias frames at the
        given gain and camera offset are used when there are any; otherwise
        every gain and offset is used, with a warning. A binning that no bias
        frame has gives an empty list. When `iso` is not given, every gain and
        offset is used. When `binning` is not given, every binning is used.

        Parameters
        ----------
        camera : `str`, optional
            The camera name.
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`.
        iso : `Any`, optional
            The light frames' gain or ISO.
        offset : `Any`, optional
            The light frames' camera offset.
        binning : `Any`, optional
            The light frames' binning, such as ``"2x2"``.
        **kwargs : `Any`
            Ignored; accepted so older call sites keep working.

        Returns
        -------
        frames : `list` [`str`]
            Matching bias frame file paths, filtered to existing
            files if `validate_paths` is `True`.
        """
        return self.select_bias_frames(camera, validate_paths, iso, offset, binning).frames

    def select_flat_frames(
        self,
        telescope: str | None = None,
        camera: str | None = None,
        filter_type: Any | None = None,
        validate_paths: bool = True,
        iso: Any | None = None,
        offset: Any | None = None,
        binning: Any | None = None,
        light_label: str = _DEFAULT_LIGHT_LABEL,
    ) -> CalibrationSelection:
        """Choose the flat frames for a batch of lights, and how well they fit.

        The binning must match exactly; if flats exist for the filter but none
        has the lights' binning, none is chosen and a blocking flag says why.
        Flats at the lights' gain and offset are used when there are any;
        otherwise every gain and offset is used, with a soft flag. Flats have
        no temperature slot.

        Parameters
        ----------
        telescope : `str`, optional
            The telescope the flats were taken through. When the library has
            no flats for it, the first telescope's flats are used (so offline
            stacking works without telescope metadata).
        camera : `str`, optional
            The camera name.
        filter_type : `Any`, optional
            The filter. Older and newer names of the same filter match (see
            `FLAT_FILTER_ALIASES`).
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`.
        iso : `Any`, optional
            The light frames' gain or ISO. When `None`, every gain and offset
            is used with no flag.
        offset : `Any`, optional
            The light frames' camera offset.
        binning : `Any`, optional
            The light frames' binning, such as ``"2x2"``. When `None`, every
            binning is used with no flag.
        light_label : `str`, optional
            How the flag sentences name the lights.

        Returns
        -------
        selection : `CalibrationSelection`
            The flat file paths, with the soft and blocking flag sentences.
        """
        search_filters = flat_filter_names(filter_type)

        telescope_data = self.flat_frames.get(telescope, {})
        if not telescope_data and len(self.flat_frames) > 0:
            # Fallback to the first telescope if not found, to
            # support offline stacking without telescope metadata
            telescope_data = next(iter(self.flat_frames.values()))

        camera_data = self._get_camera_dict(telescope_data, camera)

        slots: dict[str, list[str]] = {}
        with self._lock:
            for f_name in search_filters:
                for setting_key, paths in camera_data.get(f_name, {}).items():
                    if isinstance(paths, list):
                        slots.setdefault(setting_key, []).extend(_existing(paths, validate_paths))
        chosen, flags, blocking_flags = _choose_slots(slots, iso, offset, binning, "flat", light_label)
        for flag in [*flags, *blocking_flags]:
            logger.warning(flag)
        frames = _without_duplicates([path for paths in chosen.values() for path in paths])
        return CalibrationSelection(frames, flags, blocking_flags)

    def get_flat_frames(
        self,
        telescope: str | None = None,
        camera: str | None = None,
        filter_type: Any | None = None,
        validate_paths: bool = True,
        iso: Any | None = None,
        offset: Any | None = None,
        binning: Any | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Retrieve flats for a telescope, camera, filter and settings.

        This is `select_flat_frames` without the flags. Flats at the given
        gain and camera offset are used when there are any for the filter;
        otherwise every gain and offset is used, with a warning. A binning
        that no flat has gives an empty list. When `iso` is not given, every
        gain and offset is used. When `binning` is not given, every binning
        is used.

        Parameters
        ----------
        telescope : `str`, optional
            The telescope the flats were taken through.
        camera : `str`, optional
            The camera name.
        filter_type : `Any`, optional
            The filter.
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`.
        iso : `Any`, optional
            The light frames' gain or ISO.
        offset : `Any`, optional
            The light frames' camera offset.
        binning : `Any`, optional
            The light frames' binning, such as ``"2x2"``.
        **kwargs : `Any`
            Ignored; accepted so older call sites keep working.

        Returns
        -------
        frames : `list` [`str`]
            Matching flat frame file paths, filtered to existing
            files if `validate_paths` is `True`.
        """
        return self.select_flat_frames(
            telescope, camera, filter_type, validate_paths, iso, offset, binning
        ).frames

    def list_flat_groups(
        self,
        telescope: str | None = None,
        camera: str | None = None,
        filter_type: Any = None,
        gain: Any = None,
        offset: Any = None,
        validate_paths: bool = True,
    ) -> list[FlatGroup]:
        """List the flat sets in the library, one per gain, offset and binning.

        Flats at different gains, offsets or binnings never mix in one group,
        because a flat only calibrates lights taken at the same settings.
        Leave an argument as `None` to include every value of it.

        Parameters
        ----------
        telescope : `str`, optional
            Only this telescope's flats.
        camera : `str`, optional
            Only this camera's flats. Other spellings of the name match.
        filter_type : `Any`, optional
            Only flats for this filter. Older and newer names of the same
            filter match (see `FLAT_FILTER_ALIASES`).
        gain : `Any`, optional
            Only flats at this gain.
        offset : `Any`, optional
            With `gain`, only flats at this camera offset.
        validate_paths : `bool`, optional
            Drop files that no longer exist, by default `True`. A group left
            with no files is dropped.

        Returns
        -------
        groups : `list` [`FlatGroup`]
            The matching flat sets, in the order the library holds them.
        """
        wanted_names = None
        if filter_type is not None:
            wanted_names = {name.upper() for name in flat_filter_names(filter_type)}
        groups = []
        with self._lock:
            for telescope_name, camera_index in self.flat_frames.items():
                if telescope is not None and telescope_name != telescope:
                    continue
                camera_key = None if camera is None else self._find_camera_key(camera_index, camera)
                if camera is not None and camera_key is None:
                    continue
                for camera_name, filter_index in camera_index.items():
                    if camera_key is not None and camera_name != camera_key:
                        continue
                    for filter_name, setting_index in filter_index.items():
                        if wanted_names is not None and filter_name.upper() not in wanted_names:
                            continue
                        for setting_key, paths in setting_index.items():
                            if gain is not None and not _is_same_setting(setting_key, gain, offset):
                                continue
                            kept = [p for p in paths if os.path.exists(p)] if validate_paths else list(paths)
                            if not kept:
                                continue
                            setting = parse_calibration_setting_key(setting_key)
                            group = FlatGroup(
                                telescope_name,
                                camera_name,
                                filter_name,
                                setting.gain,
                                setting.offset,
                                kept,
                                setting.binning,
                            )
                            groups.append(group)
        return groups

    def refresh_dark_frames(self, prune_missing: bool = False) -> None:
        """Rescan the dark-frames directory and re-add any FITS files found.

        Parameters
        ----------
        prune_missing : `bool`, optional
            If `True`, clear `dark_frames` before rescanning so
            entries for files that no longer exist are dropped, by
            default `False`.
        """
        if not self.app_config:
            return
        if prune_missing:
            self.dark_frames = {}

        image_file_path = self.app_config.get_frames_path() / "darks" / ""
        if not os.path.exists(image_file_path):
            return

        for root, _, files in os.walk(image_file_path):
            for file in files:
                file_path = os.path.join(root, file)
                if any(x in file_path for x in ["/Dark/", "/Bias/", "/Flat/"]):
                    continue
                if os.path.isfile(file_path):
                    self.add_dark_frame(file_path)

    def refresh_bias_frames(self, prune_missing: bool = False) -> None:
        """Rescan the bias-frames directory and re-add any FITS files found.

        Parameters
        ----------
        prune_missing : `bool`, optional
            If `True`, clear `bias_frames` before rescanning so
            entries for files that no longer exist are dropped, by
            default `False`.
        """
        if not self.app_config:
            return
        if prune_missing:
            self.bias_frames = {}

        image_file_path = self.app_config.get_frames_path() / "biases" / ""
        if not os.path.exists(image_file_path):
            return

        for root, _, files in os.walk(image_file_path):
            for file in files:
                file_path = os.path.join(root, file)
                if any(x in file_path for x in ["/Dark/", "/Bias/", "/Flat/"]):
                    continue
                if os.path.isfile(file_path):
                    self.add_bias_frame(file_path)

    def refresh_flat_frames(self, prune_missing: bool = False) -> None:
        """Rescan the flat-frames directory and re-add any FITS files found.

        Iterates each per-telescope subdirectory under the flats
        root, since flat frames are organized by telescope.

        Parameters
        ----------
        prune_missing : `bool`, optional
            If `True`, clear `flat_frames` before rescanning so
            entries for files that no longer exist are dropped, by
            default `False`.
        """
        if not self.app_config:
            return
        if prune_missing:
            self.flat_frames = {}

        base_flats_path = self.app_config.get_frames_path() / "flats" / ""
        if not os.path.exists(base_flats_path):
            return

        for telescope in os.listdir(base_flats_path):
            image_file_path = os.path.join(base_flats_path, telescope)
            if os.path.isdir(image_file_path):
                for root, _, files in os.walk(image_file_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        if any(x in file_path for x in ["/Dark/", "/Bias/", "/Flat/"]):
                            continue
                        if os.path.isfile(file_path):
                            self.add_flat_frame(file_path, telescope)


# Names a flat's filter may be filed under. Flat folders and FITS headers use
# older and newer names for the same filter, so a lookup for one name must
# also find the others. A frame with no filter counts as luminance.
FLAT_FILTER_ALIASES: dict[str, list[str]] = {
    "L": ["Luminance", "L", "None", "NONE", ""],
    "Luminance": ["Luminance", "L", "None", "NONE", ""],
    "SPEC": ["Star Analyzer 200", "SPEC", "SA200"],
    "Star Analyzer 200": ["Star Analyzer 200", "SPEC", "SA200"],
    "None": ["None", "NONE", "", "Luminance", "L"],
    "NONE": ["None", "NONE", "", "Luminance", "L"],
    "": ["None", "NONE", "", "Luminance", "L"],
}


def flat_filter_names(filter_type: Any) -> list[str]:
    """List every name a flat for this filter may be filed under.

    Parameters
    ----------
    filter_type : `Any`
        A `FilterType` or a filter name.

    Returns
    -------
    names : `list` [`str`]
        The filter's own name first, then its aliases. A name with no
        aliases gives a list of just that name.
    """
    name = filter_type.value if hasattr(filter_type, "value") else str(filter_type)
    return list(FLAT_FILTER_ALIASES.get(name, [name]))


def is_calibration_gain_compatible(light_gain: str, master_gain: str) -> bool:
    """Check if the calibration and light frames share the same gain.

    Gain (or ISO) is how sensitive the camera is set to be. All calibration
    files (darks, bias, and flats) must have the same gain as the light
    images for the math to work correctly. Gains are compared as numbers, so
    "800" and "800.0" are the same gain.

    Parameters
    ----------
    light_gain : `str`
        The gain setting of the light images.
    master_gain : `str`
        The gain setting of the calibration file.

    Returns
    -------
    is_compatible : `bool`
        True if the gain settings match, False if they don't.
    """
    return iso_or_gain_values_match(light_gain, master_gain)


def is_calibration_offset_compatible(light_offset: Any, master_offset: Any) -> bool:
    """Check if the calibration and light frames share the same camera offset.

    The offset is the baseline the camera adds to every pixel. Calibration
    frames taken at a different offset carry a different baseline, so
    subtracting them shifts every pixel by the difference. An offset that was
    not recorded counts as 0.

    Parameters
    ----------
    light_offset : `Any`
        The camera offset of the light images.
    master_offset : `Any`
        The camera offset of the calibration file.

    Returns
    -------
    is_compatible : `bool`
        True if the two offsets are the same number.
    """
    light_value, master_value = _as_float(light_offset), _as_float(master_offset)
    return (0.0 if light_value is None else light_value) == (0.0 if master_value is None else master_value)


def is_dark_calibration_metadata_compatible(
    light_exposure: float,
    light_gain: str,
    master_exposure: float,
    master_gain: str,
    exposure_tolerance_seconds: float = 1.0,
) -> bool:
    """Check if a dark calibration file matches the light images.

    Dark frames remove thermal noise, which builds up over time. Because
    of this, a dark frame must have both the exact same gain AND very
    close to the same exposure time as the light image.

    (Note: Do not use this for bias or flat files, because their exposure
    times are supposed to be different from the light images.)

    Parameters
    ----------
    light_exposure : `float`
        The exposure time of the light images, in seconds.
    light_gain : `str`
        The gain setting of the light images.
    master_exposure : `float`
        The exposure time of the dark file, in seconds.
    master_gain : `str`
        The gain setting of the dark file.
    exposure_tolerance_seconds : `float`, optional
        How much difference in exposure time is allowed (default 1.0 seconds).

    Returns
    -------
    is_compatible : `bool`
        True if the gain matches perfectly and the exposure is close enough.
    """
    if not is_calibration_gain_compatible(light_gain, master_gain):
        return False
    return abs(float(light_exposure) - float(master_exposure)) <= exposure_tolerance_seconds


def is_dark_calibration_temperature_compatible(
    light_temperature_c: float | None,
    master_temperature_c: float | None,
    temperature_tolerance_c: float = DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
) -> bool:
    """Check if a dark master's capture temperature suits the light frames.

    Dark current (and so the correct pixel-level subtraction) changes
    with sensor temperature, so a dark captured at a very different
    temperature than the lights it calibrates can under- or
    over-subtract -- the latter shows up as Siril's "many negative
    pixels" warning. `get_dark_frames` does not consider temperature at
    all, so this is a separate, additive check for callers to apply
    once they already have a matched master in hand.

    Parameters
    ----------
    light_temperature_c : `float` or `None`
        A representative sensor temperature (e.g. the mean across the
        group) for the light frames being calibrated. `None` if it was
        never recorded.
    master_temperature_c : `float` or `None`
        The dark master's own capture temperature. `None` if it was
        never recorded.
    temperature_tolerance_c : `float`, optional
        How many degrees C apart they may be before this reports
        incompatible.

    Returns
    -------
    is_compatible : `bool`
        True if either temperature is unknown (nothing to judge) or
        they are within tolerance of each other.
    """
    if light_temperature_c is None or master_temperature_c is None:
        return True
    return abs(light_temperature_c - master_temperature_c) <= temperature_tolerance_c
