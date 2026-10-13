"""Purpose: Judge whether a night's capture data is good.

Description: Looks at how the frames were gathered, before anyone interprets
what they show. A night whose exposures never reached the frame library, or
whose frames lack the measurements later steps need, gives an analysis that
describes only part of the night.

Three checks compare the library with what Ekos (the telescope's capture
software) says it did:

1. Every light exposure Ekos finished should have a frame in the library.
   Ekos logs when an exposure ends, and the frame records when it began, so
   the two are matched by time, not by file name (older Ekos logs have no file
   name). Exposures whose saved path Ekos logged as a dark, bias or flat are
   calibration frames and are not expected among the light frames. Those with
   no logged path cannot be classified, so they are counted separately and
   never treated as missing.
2. The share of exposures Ekos cancelled is compared with the level that 90
   percent of this equipment's earlier nights stayed under.
3. Each frame should carry the measurements later steps use.

A check whose limit cannot yet be worked out reports `None`: the night is
neither passed nor failed.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from astrometricslib import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
from wayfindinglib.analytics.performance_envelope import MINIMUM_FRAMES_PER_NIGHT
from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.capture_quality import CaptureInputQuality
from wayfindinglib.models.session.ekos_session import EkosCapture
from wayfindinglib.tasks.control_tasks.capture_analysis_tasks import capture_kind

MATCH_WINDOW_SECONDS = 30.0
"""Farthest apart an Ekos exposure and a frame may start and still match.

Real nights show the two starting within about 2 seconds of each other (the
camera's readout and download time), with a few pairs up to about 10 seconds
apart. 30 seconds covers them all, yet is shorter than the gap between most
exposures, and each frame is used once, so a frame is not matched to a
neighbouring exposure by accident.
"""


@dataclass
class CaptureMatch:
    """How a night's Ekos exposures line up with its frames.

    Attributes
    ----------
    pairs : `int`
        Exposures that have a frame.
    unmatched_captures : `list` [`EkosCapture`]
        Exposures with no frame.
    unmatched_frames : `list` [`CaptureFrame`]
        Frames no exposure accounts for.
    """

    pairs: int
    unmatched_captures: list[EkosCapture]
    unmatched_frames: list[CaptureFrame]


def match_captures_to_frames(captures: Sequence[EkosCapture], frames: Sequence[CaptureFrame]) -> CaptureMatch:
    """Pair each Ekos exposure with the frame that began when it did.

    An exposure began at its completion time minus its length. Each exposure
    takes the nearest frame within `MATCH_WINDOW_SECONDS`, closest pairs
    first, and a frame is used once.

    Parameters
    ----------
    captures : `Sequence` [`EkosCapture`]
        The exposures to match.
    frames : `Sequence` [`CaptureFrame`]
        The night's light frames.

    Returns
    -------
    match : `CaptureMatch`
        The pairs and what is left over on each side.
    """
    candidates = []
    for capture_index, capture in enumerate(captures):
        started_at = capture.completed_at - capture.exposure_seconds
        for frame_index, frame in enumerate(frames):
            offset = frame.timestamp - started_at
            if abs(offset) <= MATCH_WINDOW_SECONDS:
                candidates.append((abs(offset), offset, capture_index, frame_index))
    candidates.sort()
    used_captures: set[int] = set()
    used_frames: set[int] = set()
    chosen: list[tuple[float, int, int]] = []
    for _, offset, capture_index, frame_index in candidates:
        if capture_index in used_captures or frame_index in used_frames:
            continue
        used_captures.add(capture_index)
        used_frames.add(frame_index)
        chosen.append((offset, capture_index, frame_index))

    matched_captures = {capture_index for _, capture_index, _ in chosen}
    matched_frames = {frame_index for _, _, frame_index in chosen}
    return CaptureMatch(
        pairs=len(chosen),
        unmatched_captures=[c for i, c in enumerate(captures) if i not in matched_captures],
        unmatched_frames=[f for i, f in enumerate(frames) if i not in matched_frames],
    )


def _limit(envelope: PerformanceEnvelope | None, name: str, limits_equipment_match: str) -> float | None:
    """Read one limit, or `None` if none applies to this night's equipment.

    Returns
    -------
    limit : `float` or `None`
        The limit's value, or `None` if the night's equipment does not match
        the equipment the limits were worked out for, or the limit could not
        be worked out.
    """
    if envelope is None or limits_equipment_match == "none":
        return None
    return envelope.value(name)


def _missing_measurements(frames: Sequence[CaptureFrame]) -> dict[str, int]:
    """Count the frames that lack each measurement later steps use.

    Star width and roundness are not counted: the science library measures
    them only for frames it stacked, so most frames lack them by design.

    Returns
    -------
    missing : `dict` [`str`, `int`]
        Each measurement at least one frame lacks, with how many do.
    """
    measurements = {
        "saturated_pixel_fraction": lambda frame: frame.saturated_pixel_fraction,
        "background_adu": lambda frame: frame.background_adu,
        "pixel_scale_arcsec": lambda frame: frame.pixel_scale_arcsec,
        "altitude_degrees": lambda frame: frame.altitude_degrees,
        "sensor_temperature_c": lambda frame: frame.sensor_temperature_c,
    }
    counts = {name: sum(1 for frame in frames if read(frame) is None) for name, read in measurements.items()}
    return {name: count for name, count in counts.items() if count}


def assess_capture_input_quality(
    frames: Sequence[CaptureFrame],
    captures: Sequence[EkosCapture],
    aborted_captures: int,
    has_ekos_record: bool,
    envelope: PerformanceEnvelope | None,
    limits_equipment_match: str,
) -> CaptureInputQuality:
    """Assess how complete and usable a night's capture data is.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        The night's light frames taken with the equipment in use.
    captures : `Sequence` [`EkosCapture`]
        Every exposure Ekos finished that night.
    aborted_captures : `int`
        How many exposures Ekos cancelled that night.
    has_ekos_record : `bool`
        Whether any Ekos session record exists for the night.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits.
    limits_equipment_match : `str`
        How well the night's equipment matches the one the limits are for.

    Returns
    -------
    input_quality : `CaptureInputQuality`
        The assessment.
    """
    quality = CaptureInputQuality(
        light_frames=len(frames),
        spectral_frames=sum(1 for frame in frames if frame.is_spectral),
        imaging_frames=sum(1 for frame in frames if not frame.is_spectral),
        has_ekos_record=has_ekos_record,
        has_enough_frames=len(frames) >= MINIMUM_FRAMES_PER_NIGHT,
        limits_equipment_match=limits_equipment_match,
        dark_temperature_tolerance_c=DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    )
    quality.frames_missing_measurements = _missing_measurements(frames)
    quality.has_missing_measurements = bool(quality.frames_missing_measurements)

    temperatures = [frame.sensor_temperature_c for frame in frames if frame.sensor_temperature_c is not None]
    if temperatures:
        median_temperature = statistics.median(temperatures)
        quality.sensor_temperature_spread_c = max(temperatures) - min(temperatures)
        quality.frames_outside_dark_tolerance = sum(
            1
            for temperature in temperatures
            if abs(temperature - median_temperature) > DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
        )

    if not has_ekos_record:
        return quality

    light_captures = [capture for capture in captures if capture_kind(capture) != "calibration"]
    quality.ekos_light_captures = len(light_captures)
    quality.ekos_calibration_captures = len(captures) - len(light_captures)
    match = match_captures_to_frames(light_captures, frames)
    quality.match_window_seconds = MATCH_WINDOW_SECONDS
    quality.captures_without_frame = sum(
        1 for capture in match.unmatched_captures if capture_kind(capture) == "light"
    )
    quality.captures_without_frame_of_unknown_kind = sum(
        1 for capture in match.unmatched_captures if capture_kind(capture) == "unknown"
    )
    quality.frames_without_capture = len(match.unmatched_frames)

    attempts = len(captures) + aborted_captures
    quality.aborted_captures = aborted_captures
    if attempts:
        quality.abort_fraction = aborted_captures / attempts
        limit = _limit(envelope, "capture_abort_fraction_high_limit", limits_equipment_match)
        quality.abort_fraction_limit = limit
        if limit is not None:
            quality.has_frequent_aborts = quality.abort_fraction > limit
    return quality
