"""Purpose: Choose the dark, bias and flat frames for a batch of lights.

Description: Stacking calibrates each batch of lights (one exposure group)
with the dark, bias and flat frames the calibration library picks for the
batch's camera, gain, offset, binning, temperature, exposure and filter.
Those choices were made inline while the Siril work folder was built, so
nothing else could ask "what would stacking use?" without staging files.
`choose_batch_calibration` and `choose_flat_bias` now make the choices and
change nothing on disk. The Siril staging code and the stack check
(`Astrometrics.processing.check_stack`) both call them, so the two can
never disagree.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from astrometricslib._internal.drivers.calibration_library import (
    CalibrationLibrary,
    CalibrationSelection,
    format_binning,
    header_binning,
)
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header

logger = logging.getLogger(__name__)

__all__ = [
    "BatchCalibration",
    "FlatBiasChoice",
    "choose_batch_calibration",
    "choose_flat_bias",
    "describe_bias_settings",
    "dominant_exposure",
    "representative_light_binning",
    "representative_light_temperature_c",
]


def _field(frame: Any, name: str, default: Any) -> Any:
    """Read one field of a frame record or of its dictionary form.

    Returns
    -------
    value : `Any`
        The field, or ``default`` when the frame has none.
    """
    if isinstance(frame, dict):
        return frame.get(name, default)
    return getattr(frame, name, default)


def describe_bias_settings(selection: CalibrationSelection) -> str:
    """Name the gain and offset of the bias frames a selection chose.

    Parameters
    ----------
    selection : `CalibrationSelection`
        A bias selection.

    Returns
    -------
    text : `str`
        For example ``"gain 100, offset 0"``; several settings are joined
        with " or ".
    """
    pairs = sorted({(setting.gain, setting.offset) for setting in selection.chosen_settings})
    return " or ".join(f"gain {gain}, offset {offset:g}" for gain, offset in pairs) or "unknown settings"


def dominant_exposure(frames: list[Any]) -> str:
    """Find the exposure length that most of a batch of frames share.

    The dark master is chosen by exposure length, so a batch is calibrated with
    the dark of its most common length. Before this, the dark of the *first*
    frame's length was applied to every frame, which put a 600 s dark on 300 s
    frames in the NGC 2244 and NGC 2903 sessions (2026-01-17/18) and no dark at
    all on the NGC 1893 session, whose first frame had no dark.

    Parameters
    ----------
    frames : `list`
        Frame records or dictionaries of them, with an ``exposure``.

    Returns
    -------
    exposure : `str`
        The most common exposure, as it was written on the frames (ties go to
        the one that appears first); ``"0"`` when no frame has one.
    """
    counts: dict[str, int] = {}
    for frame in frames:
        value = frame.get("exposure") if isinstance(frame, dict) else getattr(frame, "exposure", None)
        if value is None:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "0"
    return max(counts, key=counts.__getitem__)


def representative_light_temperature_c(frames: list[Any]) -> float | None:
    """Find a light-frame batch's mean sensor temperature, for dark matching.

    The calibration library files darks by temperature slot and picks the
    slot nearest this value (see `CalibrationLibrary.select_dark_frames`), so
    this is where a batch's actual sensor temperature enters the match.

    A dict frame here is always a plain ``model_dump()`` (see
    `_stack_one_batch`), which uses the Python field name
    (``sensor_temperature_c``), not the camelCase JSON alias
    (``sensorTemperatureC``) -- confirmed against a real incident where
    checking the alias silently found no temperature on every dict
    frame, so Arcturus's dark-temperature mismatch (a dark master at
    ~0C applied to light frames that drifted from -2.9C to -10.7C,
    which Siril reported as 57-64% negative pixels after dark
    subtraction) went undetected even after this check was added.

    Parameters
    ----------
    frames : `list`
        Frame records or dictionaries of them, with a
        ``sensor_temperature_c``.

    Returns
    -------
    mean_temperature_c : `float` or `None`
        The mean of every frame's recorded temperature, or `None` if
        none of them have one.
    """
    temperatures = [
        (
            frame.get("sensor_temperature_c")
            if isinstance(frame, dict)
            else getattr(frame, "sensor_temperature_c", None)
        )
        for frame in frames
    ]
    temperatures = [t for t in temperatures if t is not None]
    return sum(temperatures) / len(temperatures) if temperatures else None


def representative_light_binning(frames: list[Any], light_paths: list[str]) -> str:
    """Find the binning a batch of light frames was taken at, for matching.

    The calibration library files frames by binning, and a frame binned
    differently can never calibrate the lights. The binning is read from the
    header of the first readable light (its ``XBINNING`` and ``YBINNING``
    cards). One light speaks for the batch, because lights binned differently
    have different image sizes and the stacker keeps only the most common
    size. If no header can be read, the binning recorded on the frame
    records is used (the most common value; the width factor stands for
    both axes), and without one, no binning (``"1x1"``).

    Parameters
    ----------
    frames : `list`
        Frame records or dictionaries of them, with a ``binning``.
    light_paths : `list` [`str`]
        The light frame paths that can be read, in the order to try them.

    Returns
    -------
    binning : `str`
        The binning as text, such as ``"2x2"``.
    """
    for path in light_paths:
        try:
            return header_binning(read_header(path))
        except FITS_READ_ERRORS:
            continue
    counts: dict[int, int] = {}
    for frame in frames:
        value = frame.get("binning") if isinstance(frame, dict) else getattr(frame, "binning", None)
        if value is not None:
            counts[int(value)] = counts.get(int(value), 0) + 1
    return format_binning(max(counts, key=counts.__getitem__)) if counts else format_binning()


@dataclass(frozen=True)
class BatchCalibration:
    """The calibration frames chosen for one batch of lights, and why.

    Attributes
    ----------
    camera, telescope, filter_name, gain, offset, binning : `str`
        The lights' settings the choice was made for.
    exposure : `str`
        The exposure length the darks were chosen for (the batch's most
        common one).
    temperature_c : `float` or `None`
        The batch's mean sensor temperature, when its frames record one.
    dark, bias, flat : `CalibrationSelection`
        What the library chose, with its soft and blocking flags.
    """

    camera: str
    telescope: str
    filter_name: str
    gain: str
    offset: str
    binning: str
    exposure: str
    temperature_c: float | None
    dark: CalibrationSelection
    bias: CalibrationSelection
    flat: CalibrationSelection


@dataclass(frozen=True)
class FlatBiasChoice:
    """The bias chosen for the flats, when it differs from the lights' bias.

    Attributes
    ----------
    frames : `list` [`str`]
        The bias frames to use for the flats only. Empty when the lights'
        bias also serves the flats.
    notes : `list` [`str`]
        Plain sentences for a mismatch the choice worked around.
    log_line : `str`
        The information line stacking writes when it stages a flat bias.
        Empty when none is needed.
    """

    frames: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    log_line: str = ""


def choose_batch_calibration(
    library: CalibrationLibrary, frames: list[Any], light_paths: list[str]
) -> BatchCalibration:
    """Choose the darks, bias and flats for one batch of light frames.

    The choice uses the first frame's camera, telescope, filter, gain and
    offset, the batch's most common exposure, its mean sensor temperature
    and the binning in its first readable light. Nothing is staged or
    written.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library of calibration frames.
    frames : `list`
        The batch's frame records (or dictionaries of them), not empty.
    light_paths : `list` [`str`]
        The batch's readable light file paths, in the order to try them for
        the binning (see `representative_light_binning`).

    Returns
    -------
    choice : `BatchCalibration`
        The three selections and the settings they were made for.
    """
    first = frames[0]
    telescope = _field(first, "telescope", "Unknown")
    camera = _field(first, "camera", "Unknown")
    gain = _field(first, "iso", "800")
    offset = _field(first, "offset", "0")
    filter_type = _field(first, "filter", "None")
    # Take the length most of the batch has, not the first frame's: a batch
    # can hold a few frames of another length (small exposure groups are
    # folded into their nearest neighbour).
    exposure = dominant_exposure(frames)
    temperature_c = representative_light_temperature_c(frames)
    binning = representative_light_binning(frames, light_paths)
    first_light = light_paths[0] if light_paths else ""
    light_label = f"the {len(light_paths)} light frame(s) starting with {os.path.basename(first_light)}"
    dark = library.select_dark_frames(
        camera=camera,
        exposure=exposure,
        iso=gain,
        offset=offset,
        binning=binning,
        temperature_c=temperature_c,
        light_label=light_label,
    )
    # Darks the defect job marked leave the choice here, so staging and the
    # stack check both skip them, and each mark becomes a flag sentence.
    dark = library.apply_dark_defect_verdicts(dark)
    bias = library.select_bias_frames(
        camera=camera, iso=gain, offset=offset, binning=binning, light_label=light_label
    )
    flat = library.select_flat_frames(
        telescope=telescope,
        camera=camera,
        filter_type=filter_type,
        iso=gain,
        offset=offset,
        binning=binning,
        light_label=light_label,
    )
    return BatchCalibration(
        camera=str(camera),
        telescope=str(telescope),
        filter_name=str(getattr(filter_type, "name", filter_type)),
        gain=str(gain),
        offset=str(offset),
        binning=binning,
        exposure=exposure,
        temperature_c=temperature_c,
        dark=dark,
        bias=bias,
        flat=flat,
    )


def choose_flat_bias(
    library: CalibrationLibrary,
    camera: str,
    light_binning: Any,
    flat_selection: CalibrationSelection,
    staged_flat_count: int,
    lights_bias_selection: CalibrationSelection,
) -> FlatBiasChoice:
    """Choose the bias frames that calibrate the flats, when they differ.

    A flat holds the camera's bias level, and only a bias taken at the
    flats' own gain and offset removes it. The lights are staged with the
    bias that matches the lights. The two agree unless the flats were taken
    at another offset than the lights. Then the lights' bias would leave a
    pedestal in the flat (an offset step of 10 is about 400 ADU on the
    ASI533MM).

    This picks the bias at the flats' gain and offset. If it is the same set
    the lights use, nothing extra is staged. If the library holds no bias at
    the flats' settings, the lights' bias still goes on the flats (the stack
    keeps running) and a note says so in plain words. Lights without a dark
    still use the lights' bias, which is why the two sets are kept apart.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The calibration library.
    camera : `str`
        The camera name.
    light_binning : `Any`
        The lights' binning, which the flats share.
    flat_selection : `CalibrationSelection`
        What the library chose for the flats.
    staged_flat_count : `int`
        How many flat frames are used. With none there is nothing to
        calibrate.
    lights_bias_selection : `CalibrationSelection`
        What the library chose for the lights' bias.

    Returns
    -------
    choice : `FlatBiasChoice`
        The bias frames for the flats only, the notes and the log line.
    """
    flat_settings = {(setting.gain, setting.offset) for setting in flat_selection.chosen_settings}
    if staged_flat_count == 0 or not flat_settings or not lights_bias_selection.frames:
        return FlatBiasChoice()
    lights_bias_text = describe_bias_settings(lights_bias_selection)
    if len(flat_settings) > 1:
        note = (
            "The flat frames were taken at several gain and offset settings, so no single bias "
            f"matches them all; the bias chosen for the lights ({lights_bias_text}) was applied "
            "to the flats."
        )
        return FlatBiasChoice(notes=[note])
    ((flat_gain, flat_offset),) = flat_settings
    flat_settings_text = f"gain {flat_gain}, offset {flat_offset:g}"
    selection = library.select_bias_frames(
        camera=camera,
        iso=flat_gain,
        offset=flat_offset,
        binning=light_binning,
        light_label=f"the flat frames ({flat_settings_text})",
    )
    if selection.flags:
        # Nothing at the flats' settings: say which bias is used and why.
        note = (
            f"No bias frames matched the flats' {flat_settings_text}, so the bias chosen for the "
            f"lights ({lights_bias_text}) was applied to the flats. If its offset differs from the "
            "flats' offset, the master flat keeps a pedestal."
        )
        return FlatBiasChoice(notes=[note])
    if not selection.frames or set(selection.frames) == set(lights_bias_selection.frames):
        return FlatBiasChoice()
    log_line = (
        f"The flats were taken at {flat_settings_text}, which differs from the lights' bias "
        f"({lights_bias_text}); {len(selection.frames)} bias frame(s) at the flats' settings "
        "calibrate the flats."
    )
    return FlatBiasChoice(frames=list(selection.frames), log_line=log_line)
