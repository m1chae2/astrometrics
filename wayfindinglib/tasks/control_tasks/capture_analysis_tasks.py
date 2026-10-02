"""Purpose: Gather the inputs for the capture analysis.

Description: Reads what the capture analysis needs from the science
library's frame records and from the recorded Ekos sessions, and shapes it
into plain data. The analysis stages themselves read nothing; this module is
the one place that touches the frame library.

A frame belongs to the equipment in use when its telescope and camera names
match the active ones. The camera may be spelled several ways (the FITS
header's name, the library's record name), so every spelling its profile
knows is accepted. Frames of other equipment are left out, which is how a
change of equipment starts the capture history afresh.
"""

import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from astrometricslib import frame_is_spectral, observing_night_id, resolve_camera_profile
from wayfindinglib.analytics.performance_envelope import (
    MINIMUM_ATTEMPTS_PER_NIGHT,
    MINIMUM_FRAMES_PER_NIGHT,
)
from wayfindinglib.models.session.capture_frame import CaptureFrame, StackSaturationVerdict
from wayfindinglib.models.session.ekos_session import EkosCapture, EkosSessionContext

_CALIBRATION_FOLDER_NAMES = frozenset({"dark", "bias", "flat"})
"""Folder names Ekos gives calibration exposures."""

_LIGHT_FOLDER_NAME = "light"
"""The folder name Ekos gives light exposures."""

_PROCESSED_FILE_MARKER = "processed"
"""Text in a file name that marks the output of post-processing.

A stacked image saved beside the raw frames (for example
``NGC_2403_Processed.fits``, 13,740 seconds long) is not a capture. The
science library's frame scan now skips such files. This check stays as a
second line of defence, because one recorded as a light frame would add hours
of exposure to a night that never had them.
"""


def normalize_name(name: str | None) -> str:
    """Reduce a device name to lowercase letters and digits.

    Returns
    -------
    normalized : `str`
        The normalized name, or an empty string for no name.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def equipment_spellings(camera_name: str, config: Any = None) -> set[str]:
    """List every normalized spelling of a camera's name.

    Parameters
    ----------
    camera_name : `str`
        The camera's name, written in any spelling.
    config : `AppConfiguration`, optional
        The configuration holding the camera profiles. The process-wide one
        is used when left out.

    Returns
    -------
    spellings : `set` [`str`]
        The name itself, the profile's name, its record name and its
        aliases, each normalized.
    """
    profile = resolve_camera_profile(camera_name, config)
    return {
        normalize_name(name)
        for name in (camera_name, profile.camera_name, profile.record_name, *profile.name_aliases)
        if name
    }


def _positive(value: float | None) -> float | None:
    """Return `value` if it is a positive number, otherwise `None`.

    Returns
    -------
    value : `float` or `None`
        A zero or negative measurement is a failed fit, not a measurement.
    """
    return value if value is not None and value > 0 else None


def collect_capture_frames(
    astrometrics: Any, telescope_name: str, camera_name: str, config: Any = None
) -> list[CaptureFrame]:
    """Read every light frame taken with this telescope and camera.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    telescope_name : `str`
        Name of the imaging telescope.
    camera_name : `str`
        Name of the imaging camera, in any spelling its profile knows.
    config : `AppConfiguration`, optional
        The configuration holding the camera profiles.

    Returns
    -------
    frames : `list` [`CaptureFrame`]
        The equipment's light frames that have a capture time, oldest first.
        A frame with no capture time cannot be placed in a night and is left
        out, as is the output of post-processing recorded as a light frame.
    """
    camera_spellings = equipment_spellings(camera_name, config)
    telescope_spelling = normalize_name(telescope_name)
    frames = []
    for target in astrometrics.targets.list():
        for frame in target.frames:
            if not str(frame.role).upper().endswith("LIGHT") or frame.timestamp is None:
                continue
            if _PROCESSED_FILE_MARKER in frame.path.rsplit("/", 1)[-1].lower():
                continue
            if normalize_name(frame.camera) not in camera_spellings:
                continue
            if normalize_name(frame.telescope) != telescope_spelling:
                continue
            measurements = frame.measurements
            width_px = _positive(measurements.registration_fwhm_x_px)
            pixel_scale = _positive(frame.pixel_scale_arcsec)
            frames.append(
                CaptureFrame(
                    path=frame.path,
                    target_id=target.id,
                    timestamp=float(frame.timestamp),
                    exposure_seconds=float(frame.exposure or 0.0),
                    filter_name=str(getattr(frame.filter, "value", frame.filter)),
                    is_spectral=frame_is_spectral(frame),
                    sensor_temperature_c=frame.sensor_temperature_c,
                    saturated_pixel_fraction=measurements.saturated_pixel_fraction,
                    background_adu=measurements.background_level,
                    star_width_arcsec=width_px * pixel_scale if width_px and pixel_scale else None,
                    roundness=_positive(measurements.registration_roundness),
                    altitude_degrees=frame.altitude_degrees,
                    azimuth_degrees=frame.azimuth_degrees,
                    pier_side=frame.pier_side,
                    pixel_scale_arcsec=pixel_scale,
                    binning=frame.binning or 1,
                )
            )
    frames.sort(key=lambda frame: frame.timestamp)
    return frames


def collect_stack_saturation_verdicts(astrometrics: Any) -> list[StackSaturationVerdict]:
    """Read the science library's saturation verdicts for stacked exposures.

    Each target's stacking summary lists its exposure groups and whether a
    star clips in each. The imaging stack and the spectroscopy stack have
    separate summaries.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).

    Returns
    -------
    verdicts : `list` [`StackSaturationVerdict`]
        One per target, stack kind and exposure length. A target that was
        never stacked has none.
    """
    verdicts = []
    for target in astrometrics.targets.list():
        for attribute, is_spectral in (("stacking", False), ("spectral_stacking", True)):
            stack = getattr(target, attribute, None)
            summary = getattr(stack, "quality_summary", None) if stack else None
            if summary is None:
                continue
            metrics = summary.stacking_metrics
            verdicts.extend(
                StackSaturationVerdict(
                    target_id=target.id,
                    is_spectral=is_spectral,
                    exposure_seconds=group.exposure_seconds,
                    saturated=group.saturated,
                    recommended_exposure_seconds=metrics.recommended_exposure_seconds,
                )
                for group in metrics.exposure_groups
            )
    return verdicts


def night_of(frame: CaptureFrame) -> str:
    """Name the observing night a frame was taken on.

    Returns
    -------
    night : `str`
        The local date on which the night began, for example ``"2026-09-24"``.
    """
    return observing_night_id(frame.timestamp)


def frames_by_night(frames: Iterable[CaptureFrame]) -> dict[str, list[CaptureFrame]]:
    """Group frames by the observing night they were taken on.

    Returns
    -------
    grouped : `dict` [`str`, `list` [`CaptureFrame`]]
        Each night's frames, in time order.
    """
    grouped: dict[str, list[CaptureFrame]] = defaultdict(list)
    for frame in frames:
        grouped[night_of(frame)].append(frame)
    return dict(grouped)


def usable_star_frames(frames: Iterable[CaptureFrame], minimum_exposure_seconds: float) -> list[CaptureFrame]:
    """Pick the frames whose star measurements say something about the sky.

    Spectroscopy frames are left out, since a dispersed star has no
    meaningful width. So are frames shorter than `minimum_exposure_seconds`:
    in a short exposure guiding error has no time to widen the star, and the
    bright single stars usually shot that briefly are clipped, which
    inflates their measured width.

    Returns
    -------
    usable : `list` [`CaptureFrame`]
        Imaging frames with a measured star width that are long enough.
    """
    return [
        frame
        for frame in frames
        if not frame.is_spectral
        and frame.star_width_arcsec is not None
        and frame.exposure_seconds >= minimum_exposure_seconds
    ]


MINIMUM_FRAMES_PER_EXPOSURE_LENGTH = 10
"""Fewest frames an exposure length needs to count as one in use.

An exposure length tried a few times while testing is not a length the
equipment is used with.
"""

MAXIMUM_EXPOSURE_LENGTHS = 6
"""Most exposure lengths the exposure-length view reports, most used first."""


def exposure_lengths_in_use(frames: Iterable[CaptureFrame], minimum_exposure_seconds: float) -> list[float]:
    """List the exposure lengths the equipment is actually used with.

    Only lengths long enough for guiding error to matter count, since guiding
    cannot spoil a shorter exposure.

    Parameters
    ----------
    frames : `Iterable` [`CaptureFrame`]
        The equipment's light frames.
    minimum_exposure_seconds : `float`
        Shortest exposure length that counts.

    Returns
    -------
    lengths : `list` [`float`]
        Lengths with at least `MINIMUM_FRAMES_PER_EXPOSURE_LENGTH` frames, the
        `MAXIMUM_EXPOSURE_LENGTHS` most used if there are more, shortest first.
    """
    counts: dict[float, int] = defaultdict(int)
    for frame in frames:
        if frame.exposure_seconds >= minimum_exposure_seconds:
            counts[round(frame.exposure_seconds, 3)] += 1
    used = [
        (length, count) for length, count in counts.items() if count >= MINIMUM_FRAMES_PER_EXPOSURE_LENGTH
    ]
    used.sort(key=lambda item: -item[1])
    return sorted(length for length, _ in used[:MAXIMUM_EXPOSURE_LENGTHS])


def capture_kind(capture: EkosCapture) -> str:
    """Tell what kind of exposure an Ekos capture was.

    Ekos saves each kind in its own folder (``.../Light/...``, ``.../Dark/``),
    so the saved path names the kind. A capture with no saved path (older
    Ekos versions did not log it) has an unknown kind.

    Returns
    -------
    kind : `str`
        ``"calibration"`` for a dark, bias or flat, ``"light"`` for a light,
        or ``"unknown"`` if the path says neither.
    """
    folders = {part.lower() for part in capture.file_path.split("/") if part}
    if folders & _CALIBRATION_FOLDER_NAMES:
        return "calibration"
    if _LIGHT_FOLDER_NAME in folders:
        return "light"
    return "unknown"


def night_captures(contexts: Iterable[EkosSessionContext], night: str) -> tuple[list[EkosCapture], int, bool]:
    """Collect one night's captures and aborts from the Ekos records.

    Parameters
    ----------
    contexts : `Iterable` [`EkosSessionContext`]
        Every recorded Ekos session.
    night : `str`
        The observing night.

    Returns
    -------
    captures : `list` [`EkosCapture`]
        Every exposure Ekos finished that night.
    aborted : `int`
        How many exposures it cancelled.
    has_record : `bool`
        Whether any session record exists for the night.
    """
    captures: list[EkosCapture] = []
    aborted = 0
    has_record = False
    for context in contexts:
        if context.session_id != night:
            continue
        has_record = True
        captures.extend(context.captures)
        aborted += len(context.aborted_captures)
    return captures, aborted, has_record


def collect_capture_baseline_values(
    frames: Sequence[CaptureFrame],
    contexts: Iterable[EkosSessionContext],
    minimum_exposure_seconds: float | None,
    before_night: str | None = None,
) -> dict[str, list[float]]:
    """Summarise each earlier night of this equipment's captures.

    Only nights with frames from this equipment count, so a change of
    equipment starts with an empty history. For each such night this gives
    the median star width and roundness of the imaging frames, and the share
    of exposures Ekos cancelled.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        Every light frame of the equipment in use.
    contexts : `Iterable` [`EkosSessionContext`]
        Every recorded Ekos session.
    minimum_exposure_seconds : `float` or `None`
        Shortest exposure whose star measurements count. `None` when it is
        not yet known, in which case no star-quality history is built.
    before_night : `str` or `None`, optional
        If given, only nights earlier than this one are read. Judging a night
        against a history that includes that night, or later ones, would let
        a bad night make itself look normal.

    Returns
    -------
    baseline_values : `dict` [`str`, `list` [`float`]]
        ``"night_star_width"`` (arcseconds), ``"night_star_roundness"`` and
        ``"capture_abort_fraction"``, one value per qualifying night.
    """
    contexts = list(contexts)
    widths: list[float] = []
    roundnesses: list[float] = []
    abort_fractions: list[float] = []
    for night, night_frames in sorted(frames_by_night(frames).items()):
        if before_night is not None and night >= before_night:
            continue
        if len(night_frames) < MINIMUM_FRAMES_PER_NIGHT:
            continue
        if minimum_exposure_seconds is not None:
            measured = usable_star_frames(night_frames, minimum_exposure_seconds)
            if len(measured) >= MINIMUM_FRAMES_PER_NIGHT:
                widths.append(statistics.median(frame.star_width_arcsec for frame in measured))
                night_roundness = [frame.roundness for frame in measured if frame.roundness is not None]
                if len(night_roundness) >= MINIMUM_FRAMES_PER_NIGHT:
                    roundnesses.append(statistics.median(night_roundness))
        captures, aborted, has_record = night_captures(contexts, night)
        attempts = len(captures) + aborted
        if has_record and attempts >= MINIMUM_ATTEMPTS_PER_NIGHT:
            abort_fractions.append(aborted / attempts)
    return {
        "night_star_width": widths,
        "night_star_roundness": roundnesses,
        "capture_abort_fraction": abort_fractions,
    }
